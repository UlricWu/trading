# filepath: tests/trading/backtest/steps/test_backtest_layers.py
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, call

from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.execution.fake import FakeExecution
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.backtest.market.signals import RecordedSignalBatch
from src.trading.backtest.steps.backtest_layers import PortfolioStep, RecordedOrdersStep, SignalStep
from src.trading.backtest.timing import BacktestTiming
from src.trading.core.events import CancelOrders, OrderIntent, Side, SignalEvent
from src.trading.portfolio.constructors.threshold import ThresholdConstructor
from src.trading.portfolio.state import PortfolioState


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
