# filepath: tests/trading/backtest/test_pipeline.py
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from src.observability.instrumentation import Instrumentation
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.portfolio.state import PortfolioState


def test_pipeline_owns_steps_creates_fresh_context_and_forwards_every_return() -> None:
    metrics = BacktestMetrics(mode='full_backtest', parameters={}, not_run={})
    next_metrics = metrics.model_copy(update={'not_run': {'signal': 'returned_context'}})
    state = BacktestState(PortfolioState(200, 200))
    contexts: list[BacktestContext] = []
    instrumentation = MagicMock(spec=Instrumentation)
    instrumentation.measure.side_effect = lambda name, operation, context: operation(context)

    def first(context: BacktestContext) -> BacktestContext:
        contexts.append(context)
        assert context.metrics is metrics and context.inputs is None and context.state is None
        return replace(context, state=state, metrics=next_metrics)

    def last(context: BacktestContext) -> BacktestContext:
        assert context.metrics is next_metrics and context.state is state
        return replace(context)

    first_step = SimpleNamespace(run=Mock(side_effect=first))
    last_step = SimpleNamespace(run=Mock(side_effect=last))
    steps = [first_step, last_step]
    pipeline = BacktestPipeline(steps=steps, instrumentation=instrumentation)
    steps.clear()
    result = pipeline.run(metrics=metrics)
    second = pipeline.run(metrics=metrics)

    assert contexts[0] is not contexts[1]
    assert result is not last_step.run.call_args_list[0].args[0]
    assert result.metrics is second.metrics is next_metrics
    assert result.state is second.state is state
    assert first_step.run.call_count == last_step.run.call_count == 2
    assert instrumentation.__enter__.call_count == instrumentation.__exit__.call_count == 2
    assert [call.args[1] for call in instrumentation.measure.call_args_list] == [first_step.run, last_step.run] * 2


@pytest.mark.parametrize('failed_index', range(5))
def test_failed_step_preserves_original_exception_and_stops_later_steps(failed_index: int) -> None:
    error = OSError('step unavailable')
    metrics = BacktestMetrics(mode='full_backtest', parameters={}, not_run={})
    steps = [SimpleNamespace(run=Mock(side_effect=lambda context: context)) for _ in range(5)]
    steps[failed_index].run.side_effect = error
    pipeline = BacktestPipeline(steps=steps, instrumentation=Instrumentation('run'))

    with pytest.raises(OSError) as caught:
        pipeline.run(metrics=metrics)

    assert caught.value is error
    assert error.__notes__ == ['Pipeline 执行; scope=run step=SimpleNamespace']
    assert [step.run.call_count for step in steps] == [1] * (failed_index + 1) + [0] * (4 - failed_index)
