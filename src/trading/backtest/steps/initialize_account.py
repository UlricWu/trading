# filepath: src/trading/backtest/steps/initialize_account.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import cast

from src.config.backtest_config import FakeExecutionConfig, FixedHoldingStrategyConfig, InitialAccount, RiskConfig, StrategyConfig
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestContext, BacktestInputs, BacktestState
from src.trading.backtest.execution.fill_models import ImmediateQuoteFillModel, LimitPriceFillModel
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput
from src.trading.core.equity import EquityCurve
from src.trading.orders.constraints import QuantityRules
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.constructors.factory import build_portfolio_constructor
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.trading.portfolio.state import PortfolioState
from src.trading.risk import RiskManager
from src.utils.datetime_utils import DateTimeUtils


@dataclass(frozen=True, slots=True)
class InitializeAccountStep:
    """Create independent account runtime and bind the declared execution assumptions.

    Example:
        step = InitializeAccountStep(initial_account=initial, execution=execution,
                                     strategy=strategy, risk=None)
        context = step.run(context)
    """

    initial_account: InitialAccount
    execution: FakeExecutionConfig
    strategy: StrategyConfig | None
    risk: RiskConfig | None

    def run(self, context: BacktestContext) -> BacktestContext:
        """Initialize one run without consuming its clock or producing predictions.

        Example:
            context = step.run(context)
        """
        inputs = cast(BacktestInputs, context.inputs)
        initial = self.initial_account
        config = self.execution
        state = _create_account_state(initial)
        market = ReplayMarket(inputs.market, initial_marks=context.initial_marks)
        executor = _create_execution_simulator(state=state, config=config, strategy=self.strategy)
        decisions = inputs.decisions
        decision_times = decisions.timestamps() if isinstance(decisions, OrderCommandSchedule) else tuple(decisions)
        clock = ReplayClock.from_iterable(
            (*inputs.market.timestamps(), *decision_times, *config.risk_check_ts_us, initial.ts_us, config.end_ts_us),
            start_us=initial.ts_us, end_us=config.end_ts_us,
        )
        constructor = None
        if self.strategy is not None:
            constructor = _initialize_strategy(strategy=self.strategy, initial=initial,
                market=inputs.market, quantity_rules=config.quantity_rules, clock=clock)
        else:
            context.metrics.not_run.update(signal="frozen_commands", strategy="frozen_commands")
        context.metrics.applied_risk = self.risk
        if self.risk is None:
            context.metrics.not_run["risk"] = "no_business_risk"
        if config.fill.type == "quoted":
            context.metrics.not_run.update(price_feasibility="quoted_fills", market_liquidity="quoted_fills")
        risk = RiskManager.from_config(self.risk) if self.risk is not None else None
        context.state = state
        context.market = market
        context.executor = executor
        context.constructor = constructor
        context.risk = risk
        context.clock = clock
        return context


def _create_account_state(initial: InitialAccount) -> BacktestState:
    account = PortfolioState(
        initial_cash=initial.cash, cash=initial.cash,
        positions=initial.positions, avg_cost=initial.average_cost,
        buy_qty_today={symbol: qty - initial.sellable[symbol] for symbol, qty in initial.positions.items()},
        current_day=initial.trade_date,
    )
    return BacktestState(account, equity_curve=EquityCurve(starting_peak_equity=initial.peak_equity))


def _create_execution_simulator(
    *, state: BacktestState, config: FakeExecutionConfig, strategy: StrategyConfig | None,
) -> AccountExecutionSimulator:
    fill_model = (ImmediateQuoteFillModel() if config.fill.type == "quoted"
                  else LimitPriceFillModel(FixedBPSlippageModel(config.fill.slippage_bp)))
    fees = config.fees
    return AccountExecutionSimulator(
        portfolio=state.portfolio_state, ledger=state.execution_ledger, pending_orders=state.pending_orders,
        settlement=SettlementEngine(cost_model=AShareCostModel(
            commission_rate=fees.commission_rate, stamp_tax_rate=fees.stamp_tax_rate,
            transfer_rate=fees.transfer_rate, min_commission=fees.min_commission,
        )),
        fill_model=fill_model, rules=config.quantity_rules,
        quote_rule=strategy.quote if strategy is not None else None,
    )


def _initialize_strategy(
    *, strategy: StrategyConfig, initial: InitialAccount, market: ReplayMarketInput,
    quantity_rules: Mapping[str, QuantityRules], clock: ReplayClock,
) -> PortfolioConstructor | FixedHoldingConstructor:
    if not isinstance(strategy, FixedHoldingStrategyConfig):
        return build_portfolio_constructor(strategy)
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
    constructor = FixedHoldingConstructor(
        max_positions=params.max_positions, decision_time=params.decision_time,
        exit_time=params.exit_time, open_dates=tuple(session.trade_date for session in market.sessions),
        rules=quantity_rules, exit_due=deadlines,
    )
    for deadline in deadlines.values():
        clock.schedule(ts_us=deadline)
    return constructor
