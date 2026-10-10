# filepath: tests/trading/backtest/steps/test_backtest_layers.py
from pathlib import Path
from unittest.mock import Mock

from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.market.signals import RecordedSignalBatch
from src.trading.backtest.steps.backtest_layers import PortfolioStep, SignalStep
from src.trading.backtest.timing import BacktestTiming
from src.trading.core.events import SignalEvent
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
