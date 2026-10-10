# filepath: tests/trading/backtest/test_pipeline.py
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Self
from unittest.mock import Mock

import pytest

from src import logs
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.context import BacktestContext, BacktestResultContext, BacktestState
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.portfolio.state import PortfolioState


class _Instrumentation:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def measure[T](self, name: str, operation: Callable[..., T], *args: object) -> T:
        return operation(*args)


@pytest.fixture
def metrics() -> BacktestMetrics:
    return BacktestMetrics(mode='full_backtest', parameters={}, not_run={})


def test_pipeline_uses_fresh_contexts_shared_account_and_dynamic_deadlines(metrics: BacktestMetrics) -> None:
    contexts, events = [], []
    state = BacktestState(PortfolioState(100, 100))
    def first(ctx: BacktestContext) -> BacktestContext:
        contexts.append(ctx)
        events.append(('first', ctx.timing.ts_us))
        if ctx.timing.ts_us == 1:
            ctx.requested_wakeup_ts_us.add(2)
        assert ctx.execution_target is None
        return ctx
    def second(ctx: BacktestContext) -> BacktestContext:
        events.append(('second', ctx.timing.ts_us))
        return ctx
    final = Mock(side_effect=lambda ctx: ctx)
    BacktestPipeline(clock=ReplayClock.from_iterable((1, 3), end_us=3),
        session_at=lambda ts: SimpleNamespace(trade_date='2026-07-03'),
        per_timing_steps=(SimpleNamespace(run=first), SimpleNamespace(run=second)),
        final_steps=(SimpleNamespace(run=final),), instrumentation=_Instrumentation()).run(state, metrics=metrics)
    assert events == [('first', 1), ('second', 1), ('first', 2), ('second', 2), ('first', 3), ('second', 3)]
    assert len({id(ctx) for ctx in contexts}) == 3
    assert all(ctx.state is state for ctx in contexts)
    final.assert_called_once()
    completed = final.call_args.args[0]
    assert isinstance(completed, BacktestResultContext)
    assert completed.state is state
    assert completed.metrics is metrics


def test_failed_tick_preserves_original_exception_and_stops_final_steps(metrics: BacktestMetrics) -> None:
    error = RuntimeError('source unavailable')
    final = Mock()
    pipeline = BacktestPipeline(clock=ReplayClock.from_iterable((1,)),
        session_at=lambda ts: SimpleNamespace(trade_date='2026-07-03'),
        per_timing_steps=(Mock(run=Mock(side_effect=error)),), final_steps=(final,),
        instrumentation=_Instrumentation())
    with pytest.raises(RuntimeError) as raised:
        pipeline.run(BacktestState(PortfolioState(100, 100)), metrics=metrics)
    assert raised.value is error
    assert error.__notes__ == ['回测时点; trade_date=2026-07-03 ts_us=1']
    final.run.assert_not_called()


@pytest.mark.parametrize(('times', 'expected'), [([0, .1, .2, .3], []), ([0, 5, 10, 15], [1, 2])])
def test_progress_is_throttled_and_omits_final_tick(
    times: list[float], expected: list[int], metrics: BacktestMetrics,
) -> None:
    messages = []
    sink = logs.add(lambda message: messages.append(str(message)), format='{message}',
                    filter='src.trading.backtest.pipeline')
    try:
        BacktestPipeline(clock=ReplayClock.from_iterable((1, 2, 3)),
            session_at=lambda ts: SimpleNamespace(trade_date='2026-07-03'),
            per_timing_steps=(), final_steps=(), instrumentation=_Instrumentation(),
            progress_clock=Mock(side_effect=times)).run(BacktestState(PortfolioState(100, 100)), metrics=metrics)
    finally:
        logs.remove(sink)
    assert len(messages) == len(expected)
    for message, ts in zip(messages, expected, strict=True):
        assert f'ts_us={ts} processed={ts}' in message


def test_final_chain_receives_replaced_state_and_forwards_replaced_context(metrics: BacktestMetrics) -> None:
    initial = BacktestState(PortfolioState(100, 100))
    replacement = BacktestState(PortfolioState(200, 200))
    replacement.prediction_refs.append(Path('actual.parquet'))
    replacement.decision_dates[1] = '2026-07-03'
    visited: list[BacktestState] = []
    next_metrics = metrics.model_copy(update={'not_run': {'signal': 'fixed_commands'}})

    def tick(context: BacktestContext) -> BacktestContext:
        visited.append(context.state)
        return replace(context, state=replacement)

    def first_final(context: BacktestResultContext) -> BacktestResultContext:
        assert context.state is replacement
        assert context.prediction_refs == replacement.prediction_refs
        assert context.decision_dates == {1: '2026-07-03'}
        assert context.risk_observations is replacement.risk_observations
        return replace(context, metrics=next_metrics)

    last = Mock(side_effect=lambda context: context)
    BacktestPipeline(clock=ReplayClock.from_iterable((1, 2)),
        session_at=lambda ts: SimpleNamespace(trade_date='2026-07-03'),
        per_timing_steps=(SimpleNamespace(run=tick),),
        final_steps=(SimpleNamespace(run=first_final), SimpleNamespace(run=last)),
        instrumentation=_Instrumentation()).run(initial, metrics=metrics)
    assert visited == [initial, replacement]
    last.assert_called_once()
    assert last.call_args.args[0].metrics is next_metrics


def test_failed_final_step_preserves_original_error_and_stops_chain(metrics: BacktestMetrics) -> None:
    error = OSError('facts cannot be written')
    later = Mock()
    with pytest.raises(OSError) as caught:
        BacktestPipeline(clock=ReplayClock.from_iterable((1,)),
            session_at=lambda ts: SimpleNamespace(trade_date='2026-07-03'), per_timing_steps=(),
            final_steps=(Mock(run=Mock(side_effect=error)), later),
            instrumentation=_Instrumentation()).run(BacktestState(PortfolioState(100, 100)), metrics=metrics)
    assert caught.value is error
    later.run.assert_not_called()
