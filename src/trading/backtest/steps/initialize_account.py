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
from src.trading.backtest.corporate_actions import CorporateActionIssue
from src.trading.backtest.execution.fill_models import ImmediateQuoteFillModel, LimitPriceFillModel
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput
from src.trading.core.equity import EquityCurve
from src.trading.core.events import OrderIntent
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
        decisions = inputs.decisions
        symbols = {o.symbol for o in inputs.market.observations} | initial.positions.keys()
        symbols.update(symbol for mark in inputs.market.marks for symbol in mark.prices)
        if isinstance(decisions, OrderCommandSchedule):
            symbols.update(command.symbol for ts_us in decisions.timestamps() for command in decisions.at(ts_us)
                           if isinstance(command, OrderIntent))
        rules = {symbol: config.quantity_rules.overrides.get(symbol, config.quantity_rules.default)
                 for symbol in sorted(symbols)}
        state = _create_account_state(initial)
        market = ReplayMarket(inputs.market, initial_marks=context.initial_marks)
        executor = _create_execution_simulator(state=state, config=config, strategy=self.strategy, rules=rules)
        decision_times = decisions.timestamps() if isinstance(decisions, OrderCommandSchedule) else tuple(decisions)
        clock = ReplayClock.from_iterable(
            (*inputs.market.timestamps(), *decision_times, *config.risk_check_ts_us,
             *context.corporate_actions.timestamps(context.sessions), initial.ts_us, config.end_ts_us),
            start_us=initial.ts_us, end_us=config.end_ts_us,
        )
        constructor = None
        if self.strategy is not None:
            constructor = _initialize_strategy(strategy=self.strategy, initial=initial,
                market=inputs.market, quantity_rules=rules, clock=clock)
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
    entitlements = {item.distribution.action_id: item for item in initial.distribution_entitlements}
    locked: dict[str, int] = {}
    for item in entitlements.values():
        if item.recognized and not item.shares_released:
            symbol = item.distribution.symbol
            locked[symbol] = locked.get(symbol, 0) + item.shares_qty
    account = PortfolioState(
        initial_cash=initial.cash, cash=initial.cash,
        positions=initial.positions, avg_cost=initial.average_cost,
        buy_qty_today={symbol: qty - initial.sellable[symbol] - locked.get(symbol, 0)
                       for symbol, qty in initial.positions.items()},
        current_day=initial.trade_date,
        distribution_entitlements=entitlements,
    )
    state = BacktestState(account, equity_curve=EquityCurve(starting_peak_equity=initial.peak_equity),
        distribution_terms={key: item.distribution for key, item in entitlements.items()})
    for item in entitlements.values():
        if item.recognition_issue is not None:
            action = item.distribution
            issue = CorporateActionIssue(ts_us=initial.ts_us, symbol=action.symbol,
                action_id=action.action_id, reason=item.recognition_issue, source_ref=action.source_ref)
            state.corporate_action_issues[(issue.source_ref, issue.action_id, issue.reason)] = issue
    return state


def _create_execution_simulator(
    *, state: BacktestState, config: FakeExecutionConfig, strategy: StrategyConfig | None,
    rules: Mapping[str, QuantityRules],
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
        fill_model=fill_model, rules=rules,
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
