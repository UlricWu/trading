# filepath: src/trading/backtest/components.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import cast

from src.access import Access
from src.config.backtest_config import FakeExecutionConfig, FixedHoldingStrategyConfig, InitialAccount, RiskConfig
from src.jobs.requests import (
    ExecutionEvalSubmission, FullBacktestSubmission, RiskRuleEvalSubmission,
    SignalEvalSubmission, StrategyEvalSubmission,
)
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestState
from src.trading.backtest.execution.fake import FakeExecution
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.venue.fake import LimitPriceVenue, QuotedPriceVenue, ReferenceVenue
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput
from src.trading.backtest.market.signals import ReplaySignal, prepare_signals
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.core.equity import EquityCurve
from src.trading.core.time import TradingWindow
from src.trading.execution.policies.constraints import QuoteRule
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.constructors.factory import build_portfolio_constructor
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.trading.portfolio.constructors.topk_hysteresis import TopKHysteresisConstructor
from src.trading.portfolio.state import PortfolioState
from src.trading.risk import RiskManager
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class SignalAccountComponents:
    """Provide one account's business dependencies for explicit workflow step wiring.

    Example:
        components = SignalAccountComponents(
            state=state, market=market, execution=execution, clock=clock,
            signal=signal, constructor=constructor, target_capacity=None, risk=None,
            quantity_steps={"600000": 100}, windows=windows, order_window_minutes=2,
            risk_config=None, annual_factor=252,
        )
    """

    state: BacktestState
    market: ReplayMarket
    execution: FakeExecution
    clock: ReplayClock
    signal: ReplaySignal
    constructor: PortfolioConstructor | FixedHoldingConstructor
    target_capacity: int | None
    risk: RiskManager | None
    quantity_steps: Mapping[str, int]
    windows: tuple[TradingWindow, ...]
    order_window_minutes: int
    risk_config: RiskConfig | None
    annual_factor: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity_steps", MappingProxyType(dict(self.quantity_steps)))


@dataclass(frozen=True, slots=True)
class OrderAccountComponents:
    """Bind frozen commands to one account without signal, strategy or risk dependencies.

    Example:
        components = OrderAccountComponents(
            state=state, market=market, execution=execution, clock=clock,
            commands=commands, annual_factor=252,
        )
    """

    state: BacktestState
    market: ReplayMarket
    execution: FakeExecution
    clock: ReplayClock
    commands: OrderCommandSchedule
    annual_factor: float


def prepare_market_input(*, config: FakeExecutionConfig, initial: InitialAccount, access: Access) -> ReplayMarketInput:
    """Read shared market facts and validate their calendar, account and execution coverage.

    Example:
        source = prepare_market_input(config=execution_config, initial=account_snapshot, access=access)
        market = ReplayMarket(source)
    """
    source = ReplayMarketInput.model_validate_json(config.market_ref.read_bytes(), strict=True)
    actual = access.trade_dates(start_date=source.sessions[0].trade_date, end_date=source.sessions[-1].trade_date)
    if tuple(actual) != tuple(session.trade_date for session in source.sessions):
        raise ValueError("prepared account sessions must exactly cover the formal trading calendar")
    if source.session_at(initial.ts_us).trade_date != initial.trade_date:
        raise ValueError("initial snapshot trade_date disagrees with replay start")
    source.session_at(config.end_ts_us)
    for ts_us in config.risk_check_ts_us:
        source.session_at(ts_us)
        if not initial.ts_us <= ts_us <= config.end_ts_us:
            raise ValueError("risk check is outside the simulation interval")
    if config.fill.type == "limit_price" and any(o.volume_qty is None for o in source.observations):
        raise ValueError("price-model execution requires explicit observed volume_qty")
    symbols = {o.symbol for o in source.observations} | initial.positions.keys()
    if not symbols <= config.quantity_rules.keys():
        raise ValueError("execution quantity rules must cover all observed and held symbols")
    return source


def prepare_signal(
    *, submission: SignalEvalSubmission | StrategyEvalSubmission | RiskRuleEvalSubmission | FullBacktestSubmission,
    access: Access, pm: PathManager, experiment_name: str,
) -> ReplaySignal:
    """Prepare decision inputs once and share each prediction across account replays.

    Example:
        signal = prepare_signal(submission=submission, access=access, pm=pm,
            experiment_name="backtest_2026-10-12_run")
    """
    inputs = prepare_signals(
        submission.signal,
        start=submission.start,
        end=submission.end,
        access=access,
        pm=pm,
        min_listing_calendar_days=submission.min_listing_calendar_days,
        start_ts_us=submission.initial_account.ts_us,
        end_ts_us=submission.execution.end_ts_us,
    )
    prediction_dir = pm.experiment_backtest_predictions_dir(experiment_name=experiment_name)
    return ReplaySignal(inputs, prediction_dir)


def build_signal_account_components(
    *, submission: SignalEvalSubmission | StrategyEvalSubmission | RiskRuleEvalSubmission | FullBacktestSubmission,
    market_source: ReplayMarketInput, signal: ReplaySignal, risk: RiskConfig | None,
    metrics: BacktestMetrics,
) -> SignalAccountComponents:
    """Create independent account dependencies and record their actual evaluation capabilities.

    Example:
        components = build_signal_account_components(submission=submission,
            market_source=market_source, signal=signal, risk=None, metrics=metrics)
    """
    initial_account = submission.initial_account
    execution_config = submission.execution
    strategy = submission.strategy
    state = create_state(initial_account)
    market = ReplayMarket(market_source)
    execution = create_execution(state=state, config=execution_config, quote_rule=strategy.quote)
    clock = ReplayClock.from_iterable(
        (
            *market_source.timestamps(),
            *signal.timestamps(),
            *execution_config.risk_check_ts_us,
            initial_account.ts_us,
            execution_config.end_ts_us,
        ),
        start_us=initial_account.ts_us,
        end_us=execution_config.end_ts_us,
    )
    constructor: PortfolioConstructor | FixedHoldingConstructor
    if isinstance(strategy, FixedHoldingStrategyConfig):
        params = strategy.params
        if params.initial_exit_dates.keys() != {symbol for symbol, qty in initial_account.positions.items() if qty}:
            raise ValueError("fixed_holding initial positions require explicit exit dates")
        deadlines = {
            symbol: DateTimeUtils.local_time_to_utc_epoch_us(params.exit_time, date.fromisoformat(day))
            for symbol, day in params.initial_exit_dates.items()
        }
        for deadline in deadlines.values():
            market_source.session_at(deadline)
            if deadline < initial_account.ts_us:
                raise ValueError("initial exit plan precedes account snapshot")
        constructor = FixedHoldingConstructor(
            max_positions=params.max_positions,
            decision_time=params.decision_time,
            exit_time=params.exit_time,
            open_dates=tuple(session.trade_date for session in market_source.sessions),
            rules=execution_config.quantity_rules,
            exit_due=deadlines,
        )
        for deadline in deadlines.values():
            clock.schedule(ts_us=deadline)
    else:
        constructor = build_portfolio_constructor(strategy)

    metrics.applied_risk = risk
    if risk is None:
        metrics.not_run["risk"] = "no_business_risk"
    if execution_config.fill.type != "limit_price":
        reason = f"{execution_config.fill.type}_fills"
        metrics.not_run.update(price_feasibility=reason, market_liquidity=reason)

    return SignalAccountComponents(
        state=state,
        market=market,
        execution=execution,
        clock=clock,
        signal=signal,
        constructor=constructor,
        target_capacity=constructor.max_positions if isinstance(constructor, TopKHysteresisConstructor) else None,
        risk=RiskManager.from_config(risk) if risk is not None else None,
        quantity_steps={symbol: rule.buy_step_qty for symbol, rule in execution_config.quantity_rules.items()},
        windows=tuple(window for session in market_source.sessions for window in session.windows),
        order_window_minutes=cast(int, strategy.order_window_minutes),
        risk_config=risk,
        annual_factor=execution_config.annual_factor,
    )


def build_order_account_components(
    *, submission: ExecutionEvalSubmission, market_source: ReplayMarketInput, metrics: BacktestMetrics,
) -> OrderAccountComponents:
    """Read frozen commands, bind their account and clock, and record the missing signal and risk inputs.

    Example:
        components = build_order_account_components(submission=submission,
            market_source=market_source, metrics=metrics)
    """
    initial_account = submission.initial_account
    execution_config = submission.execution
    commands = OrderCommandSchedule.from_files(
        submission.order_refs,
        start=submission.start,
        end=submission.end,
        market=market_source,
        start_ts_us=initial_account.ts_us,
        end_ts_us=execution_config.end_ts_us,
    )
    state = create_state(initial_account)
    market = ReplayMarket(market_source)
    execution = create_execution(state=state, config=execution_config, quote_rule=None)
    clock = ReplayClock.from_iterable(
        (
            *market_source.timestamps(),
            *commands.timestamps(),
            initial_account.ts_us,
            execution_config.end_ts_us,
        ),
        start_us=initial_account.ts_us,
        end_us=execution_config.end_ts_us,
    )
    metrics.applied_risk = None
    metrics.not_run.update(risk="no_business_risk", signal="frozen_commands", strategy="frozen_commands")
    return OrderAccountComponents(
        state=state,
        market=market,
        execution=execution,
        clock=clock,
        commands=commands,
        annual_factor=execution_config.annual_factor,
    )


def create_state(initial: InitialAccount) -> BacktestState:
    """Give this run its own account while retaining same-day sellable restrictions.

    Example:
        state = create_state(initial_account)
    """
    account = PortfolioState(initial_cash=initial.cash, cash=initial.cash,
        positions=initial.positions, avg_cost=initial.average_cost,
        buy_qty_today={s: qty - initial.sellable[s] for s, qty in initial.positions.items()},
        current_day=initial.trade_date)
    return BacktestState(account, equity_curve=EquityCurve(starting_peak_equity=initial.peak_equity))


def create_execution(
    *, state: BacktestState, config: FakeExecutionConfig,
    quote_rule: QuoteRule | None,
) -> FakeExecution:
    """Bind the selected venue and explicit fees to this run's account and pending orders.

    Example:
        execution = create_execution(state=state, config=config, quote_rule=None)
    """
    venue: ReferenceVenue | LimitPriceVenue | QuotedPriceVenue
    if config.fill.type == "quoted":
        venue = QuotedPriceVenue()
    elif config.fill.type == "reference":
        venue = ReferenceVenue()
    else:
        venue = LimitPriceVenue(FixedBPSlippageModel(config.fill.slippage_bp))
    fees = config.fees
    cost_model = AShareCostModel(
        commission_rate=fees.commission_rate,
        stamp_tax_rate=fees.stamp_tax_rate,
        transfer_rate=fees.transfer_rate,
        min_commission=fees.min_commission,
    )
    settlement = SettlementEngine(cost_model=cost_model)
    return FakeExecution(
        portfolio=state.portfolio_state,
        ledger=state.execution_ledger,
        pending_orders=state.pending_orders,
        settlement=settlement,
        venue=venue,
        rules=config.quantity_rules,
        quote_rule=quote_rule,
    )
