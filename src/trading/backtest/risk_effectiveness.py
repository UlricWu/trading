# filepath: src/trading/backtest/risk_effectiveness.py
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import cast

from pydantic import BaseModel, ConfigDict

from src.config.backtest_config import (
    DrawdownRiskConfig, RiskConfig, RiskRuleConfig, SingleNameRiskConfig,
)
from src.trading.core.events import Side
from src.trading.core.ledger import ExecutionLedger
from src.trading.risk.base import RiskContext
from src.trading.risk.evaluation import RiskObservation


class RiskMeasure(BaseModel):
    """Observed risk and the outstanding reduction in account currency.

    Example:
        measure = RiskMeasure(held_notional=4000, pending_buy_notional=0, value=0.4,
                              required_reduction_notional=2000, undefined_reason=None)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    held_notional: float
    pending_buy_notional: float
    value: float | None
    required_reduction_notional: float | None
    undefined_reason: str | None


class RiskControlObservation(BaseModel):
    """Risk before control and after the same tick's submission and settlement.

    Example:
        observation = RiskControlObservation(ts_us=1, before=before, after=after)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    ts_us: int
    before: RiskMeasure
    after: RiskMeasure


class RuleEffectiveness(BaseModel):
    """One configured objective's observed breaches and remaining control demand.

    Durations use left-end samples between defined observations, not continuous proof.

    Example:
        result = RuleEffectiveness(rule=rule, observations=(), sample_count=0, defined_count=0,
            observed_breach_count=0, residual_breach_count=0, max_required_reduction_notional=None,
            max_residual_reduction_notional=None, final_residual_reduction_notional=None,
            sampled_breach_duration_us=None, undefined_reason_counts={})
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    rule: RiskRuleConfig
    observations: tuple[RiskControlObservation, ...]
    sample_count: int
    defined_count: int
    observed_breach_count: int
    residual_breach_count: int
    max_required_reduction_notional: float | None
    max_residual_reduction_notional: float | None
    final_residual_reduction_notional: float | None
    sampled_breach_duration_us: int | None
    undefined_reason_counts: dict[str, int]


class RiskEffectiveness(BaseModel):
    """Absolute control outcomes plus account-wide execution diagnostics.

    Example:
        evaluation = RiskEffectiveness(rules=(result,), execution_reason_counts={"expired": 1})
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    rules: tuple[RuleEffectiveness, ...]
    execution_reason_counts: dict[str, int]


def _measure_risk(
    context: RiskContext, *, rule: RiskRuleConfig, liquidation_required: bool = False,
) -> RiskMeasure:
    holdings = {symbol: Decimal(str(context.prices[symbol])) * quantity
                for symbol, quantity in context.positions.items() if quantity}
    buys: dict[str, Decimal] = defaultdict(Decimal)
    for pending in context.pending_orders:
        if pending.intent.side == Side.BUY:
            buys[pending.intent.symbol] += (
                Decimal(str(context.prices[pending.intent.symbol])) * pending.remaining_qty
            )
    held = sum(holdings.values(), Decimal(0))
    buying = sum(buys.values(), Decimal(0))
    equity = Decimal(str(context.equity))
    value: Decimal | None = None
    reduction: Decimal | None = None
    reason = None
    if isinstance(rule, DrawdownRiskConfig):
        peak = Decimal(str(context.peak_equity))
        if peak <= 0:
            reason = "nonpositive_peak_equity"
        else:
            value = (peak - equity) / peak
            active = liquidation_required or peak - equity >= Decimal(str(rule.max_drawdown)) * peak
            reduction = held + buying if active else Decimal(0)
    elif equity <= 0:
        reason = "nonpositive_equity"
    elif isinstance(rule, SingleNameRiskConfig):
        committed = [holdings.get(symbol, Decimal(0)) + buys.get(symbol, Decimal(0))
                     for symbol in holdings.keys() | buys.keys()]
        value = max(committed, default=Decimal(0)) / equity
        cap = Decimal(str(rule.max_ratio)) * equity
        reduction = sum((max(amount - cap, Decimal(0)) for amount in committed), Decimal(0))
    else:
        value = (held + buying) / equity
        reduction = max(held + buying - Decimal(str(rule.max_gross_ratio)) * equity, Decimal(0))
    return RiskMeasure(held_notional=float(held), pending_buy_notional=float(buying),
        value=float(value) if value is not None else None,
        required_reduction_notional=float(reduction) if reduction is not None else None,
        undefined_reason=reason)


def evaluate_risk_effectiveness(
    observations: Sequence[RiskObservation], *, config: RiskConfig, ledger: ExecutionLedger,
) -> RiskEffectiveness:
    """Evaluate completed replay snapshots without executing any risk decision again.

    Every supplied observation must have the outcome recorded by RiskOutcomeStep.
    Pure frozen-case observations belong to evaluate_risk, not this account evaluation.

    Example:
        evaluation = evaluate_risk_effectiveness(observations, config=risk_config, ledger=ledger)
    """
    results = []
    for rule in config.rules:
        samples = []
        for observation in observations:
            before = _measure_risk(observation.case.context, rule=rule)
            context = observation.case.context
            liquidation_required = (
                isinstance(rule, DrawdownRiskConfig) and context.peak_equity > 0
                and Decimal(str(context.peak_equity)) - Decimal(str(context.equity))
                >= Decimal(str(rule.max_drawdown)) * Decimal(str(context.peak_equity))
            )
            after = _measure_risk(cast(RiskContext, observation.outcome), rule=rule,
                                  liquidation_required=liquidation_required)
            samples.append(RiskControlObservation(ts_us=context.ts_us, before=before, after=after))
        before_values = [sample.before.required_reduction_notional for sample in samples
                         if sample.before.required_reduction_notional is not None]
        after_values = [sample.after.required_reduction_notional for sample in samples
                        if sample.after.required_reduction_notional is not None]
        duration = sum(
            following.ts_us - current.ts_us for current, following in zip(samples, samples[1:])
            if current.after.required_reduction_notional is not None
            and current.after.required_reduction_notional > 0
            and following.before.required_reduction_notional is not None
        )
        reasons = Counter(reason for sample in samples
                          for reason in {sample.before.undefined_reason, sample.after.undefined_reason}
                          if reason is not None)
        results.append(RuleEffectiveness(rule=rule, observations=tuple(samples), sample_count=len(samples),
            defined_count=sum(sample.before.value is not None and sample.after.value is not None
                              for sample in samples),
            observed_breach_count=sum(value > 0 for value in before_values),
            residual_breach_count=sum(value > 0 for value in after_values),
            max_required_reduction_notional=max(before_values, default=None),
            max_residual_reduction_notional=max(after_values, default=None),
            final_residual_reduction_notional=samples[-1].after.required_reduction_notional if samples else None,
            sampled_breach_duration_us=duration if samples else None, undefined_reason_counts=dict(reasons)))
    return RiskEffectiveness(rules=tuple(results),
        execution_reason_counts=dict(Counter(event.reason for event in ledger.order_events)))
