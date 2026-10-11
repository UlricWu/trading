# filepath: src/trading/backtest/components.py
from __future__ import annotations

from datetime import date

from src.access import Access
from src.config.backtest_config import FakeExecutionConfig, FixedHoldingStrategyConfig, InitialAccount, StrategyConfig
from src.trading.backtest.context import BacktestState
from src.trading.backtest.execution.fake import FakeExecution
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.venue.fake import LimitPriceVenue, ReferenceVenue
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput
from src.trading.backtest.steps.backtest_layers import FixedHoldingStep, PortfolioStep
from src.trading.core.equity import EquityCurve
from src.trading.execution.policies.constraints import QuoteRule
from src.trading.portfolio.constructors.factory import build_portfolio_constructor
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.trading.portfolio.constructors.topk_hysteresis import TopKHysteresisConstructor
from src.trading.portfolio.state import PortfolioState
from src.utils.datetime_utils import DateTimeUtils


def prepare_market(*, config: FakeExecutionConfig, initial: InitialAccount, access: Access) -> ReplayMarket:
    """Validate normalized input coverage against the formal account calendar.

    Example:
        market = prepare_market(config=execution_config, initial=account_snapshot, access=access)
    """
    source = ReplayMarketInput.model_validate_json(config.market_ref.read_bytes(), strict=True)
    actual = access.trade_dates(start_date=source.sessions[0].trade_date, end_date=source.sessions[-1].trade_date)
    if tuple(actual) != tuple(session.trade_date for session in source.sessions):
        raise ValueError("prepared account sessions must exactly cover the formal trading calendar")
    market = ReplayMarket(source)
    if market.session_at(initial.ts_us).trade_date != initial.trade_date:
        raise ValueError("initial snapshot trade_date disagrees with replay start")
    market.session_at(config.end_ts_us)
    for ts_us in config.risk_check_ts_us:
        market.session_at(ts_us)
        if not initial.ts_us <= ts_us <= config.end_ts_us:
            raise ValueError("risk check is outside the simulation interval")
    if config.slippage_bp is not None and any(o.volume_qty is None for o in source.observations):
        raise ValueError("price-model execution requires explicit observed volume_qty")
    symbols = {o.symbol for o in source.observations} | initial.positions.keys()
    if not symbols <= config.quantity_rules.keys():
        raise ValueError("execution quantity rules must cover all observed and held symbols")
    return market


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
    venue: ReferenceVenue | LimitPriceVenue, quote_rule: QuoteRule | None,
) -> FakeExecution:
    """Bind the selected venue and explicit fees to this run's account and pending orders.

    Example:
        execution = create_execution(state=state, config=config, venue=ReferenceVenue(), quote_rule=None)
    """
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


def prepare_portfolio_step(
    *, strategy: StrategyConfig, market: ReplayMarket,
    execution: FakeExecutionConfig, initial: InitialAccount,
) -> PortfolioStep | FixedHoldingStep:
    """Build the selected portfolio rule and validate any initial fixed-holding exit plan.

    Example:
        step = prepare_portfolio_step(strategy=strategy, market=market,
            execution=execution_config, initial=initial_account)
    """
    if isinstance(strategy, FixedHoldingStrategyConfig):
        params = strategy.params
        if params.initial_exit_dates.keys() != {symbol for symbol, qty in initial.positions.items() if qty}:
            raise ValueError("fixed_holding initial positions require explicit exit dates")
        deadlines = {
            symbol: DateTimeUtils.local_time_to_utc_epoch_us(params.exit_time, date.fromisoformat(day))
            for symbol, day in params.initial_exit_dates.items()
        }
        for deadline in deadlines.values():
            market.session_at(deadline)
            if deadline < initial.ts_us:
                raise ValueError("initial exit plan precedes account snapshot")
        fixed_holding = FixedHoldingConstructor(
            max_positions=params.max_positions,
            decision_time=params.decision_time,
            exit_time=params.exit_time,
            open_dates=tuple(session.trade_date for session in market.source.sessions),
            rules=execution.quantity_rules,
            exit_due=deadlines,
        )
        return FixedHoldingStep(constructor=fixed_holding, market=market)

    constructor = build_portfolio_constructor(strategy)
    capacity = constructor.max_positions if isinstance(constructor, TopKHysteresisConstructor) else None
    return PortfolioStep(constructor=constructor, market=market, target_capacity=capacity)
