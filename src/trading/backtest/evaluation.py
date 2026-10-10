# filepath: src/trading/backtest/evaluation.py
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal

import numpy as np
from pydantic import BaseModel, ConfigDict

from src.trading.core.equity import EquityCurve, EquityPoint
from src.trading.core.ledger import ExecutionLedger
from src.trading.core.events import FillEvent, OrderStatus
from src.trading.risk.evaluation import RiskObservation
from src.training.evaluation import ICObservation


class SignalEvaluation(BaseModel):
    """Model-target observations with separate decision and trading-day counts.

    Example:
        result = evaluate_signals(observations, decision_dates=decision_dates)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    observations: tuple[ICObservation, ...]
    decision_count: int
    trade_day_count: int
    defined_day_count: int
    mean_ic: float | None
    daily_ic: dict[str, float | None]
    undefined_reason_counts: dict[str, int]


def evaluate_signals(
    observations: Sequence[ICObservation], *, decision_dates: Mapping[int, str],
) -> SignalEvaluation:
    """Average defined decisions within a day, then defined days equally.

    Example:
        evaluation = evaluate_signals(observations, decision_dates={decision_us: "2026-07-03"})
    """
    by_day: dict[str, list[float]] = defaultdict(list)
    for observation in observations:
        day = decision_dates[observation.decision_ts_us]
        values = by_day[day]
        if observation.value is not None:
            values.append(observation.value)
    daily = {day: float(np.mean(values)) if values else None for day, values in sorted(by_day.items())}
    defined = [value for value in daily.values() if value is not None]
    return SignalEvaluation(observations=tuple(observations), decision_count=len(observations),
        trade_day_count=len(daily), defined_day_count=len(defined),
        mean_ic=float(np.mean(defined)) if defined else None, daily_ic=daily,
        undefined_reason_counts=dict(Counter(reason for item in observations for reason in item.undefined_reasons)))


class RuleCoverage(BaseModel):
    """Count actual checks and triggers independently.

    Example:
        coverage = RuleCoverage(checked=3, triggered=0)
    """

    checked: int
    triggered: int


class RiskEvaluation(BaseModel):
    """Raw cases, exact decisions and actual rule coverage.

    Example:
        evaluation = evaluate_risk(observations, configured_rules=("max_single_name",))
    """

    observations: tuple[RiskObservation, ...]
    case_count: int
    request_count: int
    adjusted_count: int
    rejected_count: int
    rule_coverage: dict[str, RuleCoverage]


def evaluate_risk(
    observations: Sequence[RiskObservation], *, configured_rules: Sequence[str],
) -> RiskEvaluation:
    """Summarize recorded decisions without calling the rules again.

    Example:
        evaluation = evaluate_risk(observations, configured_rules=("max_single_name",))
    """
    checked = Counter(rule.rule for item in observations for rule in item.decision.rules if rule.checked)
    triggered = Counter(rule.rule for item in observations for rule in item.decision.rules if rule.triggered)
    names = tuple(dict.fromkeys((*configured_rules, *checked)))
    return RiskEvaluation(observations=tuple(observations), case_count=len(observations),
        request_count=sum(item.case.target is not None for item in observations),
        adjusted_count=sum(item.decision.adjusted is not None and item.decision.adjusted != item.case.target
                           for item in observations),
        rejected_count=sum(item.decision.reason == "rejected" for item in observations),
        rule_coverage={name: RuleCoverage(checked=checked[name], triggered=triggered[name]) for name in names})


class OrderCompletion(BaseModel):
    """One submitted order's cumulative fill quantity and elapsed wait.

    Example:
        row = OrderCompletion(order_id=1, requested_qty=400, filled_qty=360,
            remaining_qty=40, status="expired", first_fill_wait_us=60_000_000,
            completion_wait_us=None, last_reason="expired")
    """

    order_id: int
    requested_qty: int
    filled_qty: int
    remaining_qty: int
    status: str
    first_fill_wait_us: int | None
    completion_wait_us: int | None
    last_reason: str


class ExecutionEvaluation(BaseModel):
    """Completion ratios use submitted identities and quantities, never fill counts.

    Example:
        evaluation = evaluate_execution(ledger)
    """

    order_count: int
    accepted_count: int
    completed_count: int
    requested_qty: int
    filled_qty: int
    acceptance_rate: float | None
    quantity_fill_rate: float | None
    order_completion_rate: float | None
    orders: tuple[OrderCompletion, ...]
    fees: Decimal
    undefined_reasons: dict[str, str]


def evaluate_execution(ledger: ExecutionLedger) -> ExecutionEvaluation:
    """Evaluate the saved order/transition/fill facts shared by execution and full.

    Example:
        evaluation = evaluate_execution(ledger)
    """
    fills: dict[int, list[FillEvent]] = defaultdict(list)
    for fill in ledger.fills_by_id.values():
        fills[fill.order_id].append(fill)
    last = {event.order_id: event for event in ledger.order_events}
    rows = []
    for order in ledger.orders_by_id.values():
        order_fills = fills[order.order_id]
        filled = sum(fill.qty for fill in order_fills)
        event = last[order.order_id]
        rows.append(OrderCompletion(order_id=order.order_id, requested_qty=order.qty,
            filled_qty=filled, remaining_qty=order.qty - filled, status=event.status.value,
            first_fill_wait_us=order_fills[0].ts_us - order.ts_us if order_fills else None,
            completion_wait_us=order_fills[-1].ts_us - order.ts_us if filled == order.qty else None,
            last_reason=event.reason))
    count = len(rows)
    accepted = sum(row.status != OrderStatus.REJECTED.value for row in rows)
    completed = sum(row.filled_qty == row.requested_qty for row in rows)
    requested = sum(row.requested_qty for row in rows)
    filled = sum(row.filled_qty for row in rows)
    return ExecutionEvaluation(order_count=count, accepted_count=accepted, completed_count=completed,
        requested_qty=requested, filled_qty=filled, acceptance_rate=accepted / count if count else None,
        quantity_fill_rate=filled / requested if requested else None,
        order_completion_rate=completed / count if count else None, orders=tuple(rows),
        fees=sum((cost.total for cost in ledger.order_costs.values()), Decimal(0)),
        undefined_reasons={} if count else {"ratios": "no_orders"})


class PortfolioEvaluation(BaseModel):
    """Returns on one settled path, sampled at declared account-day closes.

    Example:
        evaluation = evaluate_portfolio(equity, daily_equity=daily, ledger=ledger, annual_factor=252)
    """

    initial_equity: float
    final_equity: float
    total_return: float | None
    gross_return: float | None
    total_fees: Decimal
    turnover: float | None
    sharpe: float | None
    annual_factor: float
    max_drawdown: float
    daily_returns: dict[str, float | None]
    undefined_reasons: dict[str, str]


def evaluate_portfolio(
    equity: EquityCurve, *, daily_equity: Mapping[str, EquityPoint],
    ledger: ExecutionLedger, annual_factor: float,
) -> PortfolioEvaluation:
    """Keep event wakeup frequency out of daily returns, Sharpe and turnover.

    Gross return adds fees back on the same realized path; it is not another simulation.

    Example:
        evaluation = evaluate_portfolio(equity, daily_equity=daily, ledger=ledger, annual_factor=252)
    """
    initial = equity.points[0].equity
    final = equity.points[-1].equity
    previous = initial
    returns: dict[str, float | None] = {}
    for day, point in sorted(daily_equity.items()):
        returns[day] = point.equity / previous - 1 if previous > 0 else None
        previous = point.equity
    values = [value for value in returns.values() if value is not None]
    reasons: dict[str, str] = {}
    sharpe = None
    if len(values) < 2 or len(values) != len(returns):
        reasons["sharpe"] = "insufficient_defined_daily_returns"
    else:
        deviation = float(np.std(values, ddof=1))
        if deviation == 0:
            reasons["sharpe"] = "zero_daily_volatility"
        else:
            sharpe = float(np.mean(values)) / deviation * math.sqrt(annual_factor)
    fees = sum((cost.total for cost in ledger.order_costs.values()), Decimal(0))
    notional = sum((fill.price * fill.qty for fill in ledger.fills_by_id.values()), Decimal(0))
    average = float(np.mean([initial, *(point.equity for point in daily_equity.values())]))
    if initial <= 0:
        reasons["returns"] = "nonpositive_initial_equity"
    if average <= 0:
        reasons["turnover"] = "nonpositive_average_equity"
    return PortfolioEvaluation(initial_equity=initial, final_equity=final,
        total_return=final / initial - 1 if initial > 0 else None,
        gross_return=(final + float(fees)) / initial - 1 if initial > 0 else None,
        total_fees=fees, turnover=float(notional) / average if average > 0 else None,
        sharpe=sharpe, annual_factor=annual_factor, max_drawdown=abs(equity.max_drawdown),
        daily_returns=returns, undefined_reasons=reasons)
