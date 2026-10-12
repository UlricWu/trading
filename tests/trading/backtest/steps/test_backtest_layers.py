# filepath: tests/trading/backtest/steps/test_backtest_layers.py
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, call

from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.execution.fake import FakeExecution
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.venue.fake import ReferenceVenue
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.backtest.market.signals import RecordedSignalBatch
from src.trading.backtest.steps.backtest_layers import (
    PortfolioStep, RecordedOrdersStep, RiskDecisionStep, SignalStep, SubmitTargetsStep,
)
from src.trading.backtest.timing import BacktestTiming
from src.trading.core.events import CancelOrders, OrderIntent, Side, SignalEvent, TargetEvent
from src.trading.core.time import TradingWindow
from src.trading.execution.policies.constraints import QuantityRules
from src.trading.portfolio.constructors.threshold import ThresholdConstructor
from src.trading.portfolio.state import PortfolioState
from src.trading.risk.engine import RiskManager
from src.trading.risk.rules.max_position import MaxSingleNameNotionalRule


def test_signal_step_only_consumes_current_batch_and_does_not_reset_account() -> None:
    batch = RecordedSignalBatch('2026-07-03', SignalEvent(1, {'A': .8}), ('A', 'B'), Path('/prediction'))
    state = BacktestState(PortfolioState(2000, 2000, positions={'B': 100}, current_day='2026-07-03'))
    step = SignalStep({1: batch}, Path('/unused'))
    context = BacktestContext(BacktestTiming(0, '2026-07-03'), state)
    assert step.run(context).signal is None
    context = BacktestContext(BacktestTiming(1, '2026-07-03'), state)
    assert step.run(context).signal == batch
    assert state.portfolio_state.positions == {'B': 100}
    assert state.signal_tape.events == [batch.event]
    market = Mock()
    market.prices = {'A': 10, 'B': 10}
    PortfolioStep(ThresholdConstructor(threshold=.5, target_quantity=100), market, None).run(context)
    assert context.execution_target.targets == {'A': 100, 'B': 100}
    assert state.target_tape.events == [context.execution_target]


def test_recorded_orders_step_uses_only_current_commands_in_original_order(order_market: ReplayMarket) -> None:
    order = OrderIntent(10, 'A', Side.BUY, 100, 2, 10, 90, Decimal('10'))
    replacement = OrderIntent(10, 'A', Side.BUY, 100, 1, 10, 90, Decimal('10'))
    commands = OrderCommandSchedule((order, CancelOrders(10, (2,)), replacement),
        start='2026-07-03', end='2026-07-03', market=order_market.source, start_ts_us=10, end_ts_us=50)
    execution = Mock(spec=FakeExecution)
    state = BacktestState(PortfolioState(2000, 2000, current_day='2026-07-03'))
    step = RecordedOrdersStep(execution, order_market, commands)

    for ts_us in (9, 10, 11):
        step.run(BacktestContext(BacktestTiming(ts_us, '2026-07-03'), state))

    assert execution.method_calls == [
        call.submit_orders(orders=(order,), market=order_market),
        call.cancel_orders(ts_us=10, order_ids=(2,)),
        call.submit_orders(orders=(replacement,), market=order_market),
    ]


def test_risk_step_owns_quantity_steps_for_later_decisions() -> None:
    quantity_steps = {"A": 100}
    market = Mock(spec=ReplayMarket)
    market.prices = {"A": Decimal("10")}
    step = RiskDecisionStep(RiskManager((MaxSingleNameNotionalRule(0.2),)), market, quantity_steps)
    state = BacktestState(PortfolioState(Decimal("20000"), Decimal("20000")))
    state.equity_curve.append(ts_us=1, cash=20000, market_value=0)
    context = BacktestContext(BacktestTiming(1, "2026-07-03"), state,
                              execution_target=TargetEvent(1, {"A": 1000}))

    quantity_steps["A"] = 1000
    step.run(context)

    assert context.execution_target.targets == {"A": 400}
    assert state.risk_observations[0].case.context.quantity_steps == {"A": 100}


def test_target_submission_owns_its_order_windows() -> None:
    windows = [TradingWindow("2026-07-03", 0, 180_000_000)]
    state = BacktestState(PortfolioState(Decimal("20000"), Decimal("20000"), current_day="2026-07-03"))
    market = Mock(spec=ReplayMarket)
    market.get_price.return_value = Decimal("10")
    rules = {"A": QuantityRules(100, 100, 1, 1, 1, Decimal("0.01"), True)}
    execution = FakeExecution(state.portfolio_state, state.execution_ledger, state.pending_orders,
                              SettlementEngine(), ReferenceVenue(), rules, None)
    step = SubmitTargetsStep(execution, market, windows, 2)
    context = BacktestContext(BacktestTiming(0, "2026-07-03"), state,
                              execution_target=TargetEvent(0, {"A": 100}))

    windows.clear()
    step.run(context)

    order = state.pending_orders[1].intent
    assert order.qty == 100
    assert order.expires_ts_us == 120_000_000
    assert context.requested_wakeup_ts_us == {120_000_000}
