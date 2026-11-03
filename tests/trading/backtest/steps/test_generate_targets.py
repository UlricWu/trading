# filepath: tests/trading/backtest/steps/test_generate_targets.py
from decimal import Decimal
from pathlib import Path

from src.trading.backtest.context import BacktestContext, TickState
from src.trading.backtest.market.signals import RecordedSignalBatch
from src.trading.backtest.steps.generate_targets import GenerateTargetsStep
from src.trading.core.events import OrderIntent, Side, SignalEvent
from src.trading.core.time import US_PER_MINUTE
from src.trading.portfolio.constructors.threshold import ThresholdConstructor

M = US_PER_MINUTE


def test_original_target_preserves_missing_scores_and_becomes_default_execution_target(
    tick_context: BacktestContext,
) -> None:
    account = tick_context.state.portfolio_state
    account.positions['B'] = 100
    account.avg_cost['B'] = Decimal('10')
    tick_context.executor.submit_orders(orders=(OrderIntent(0, 'B', Side.BUY, 50, 1, 0, 3 * M, Decimal('10')),))
    tick_context.constructor = ThresholdConstructor(.5, 100)
    tick_context.tick.signal = RecordedSignalBatch('2026-07-03', SignalEvent(M, {'A': .8}), ('A', 'B'), Path('/prediction'))
    step = GenerateTargetsStep()
    assert step.run(tick_context) is tick_context
    target = tick_context.tick.original_target
    assert target.targets == {'A': 100, 'B': 150}
    assert tick_context.tick.execution_target is target
    assert tick_context.state.target_tape.events == [target]

    tick_context.tick = TickState(ts_us=M + 1, session=tick_context.tick.session)
    step.run(tick_context)
    assert tick_context.tick.original_target is tick_context.tick.execution_target is None
    assert tick_context.state.target_tape.events == [target]
