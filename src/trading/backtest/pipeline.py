# filepath: src/trading/backtest/pipeline.py
from __future__ import annotations

from collections.abc import Sequence

from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.reporting.metrics import BacktestMetrics


class BacktestPipeline:
    """Run the workflow-supplied Steps once within one measured scope.

    Example:
        pipeline = BacktestPipeline(steps=steps, instrumentation=instrumentation)
        result = pipeline.run(metrics=metrics)
    """

    def __init__(self, *, steps: Sequence[PipelineStep[BacktestContext]],
                 instrumentation: Instrumentation) -> None:
        """Own the ordered run-level chain without selecting business behavior.

        Example:
            pipeline = BacktestPipeline(steps=steps, instrumentation=instrumentation)
        """
        self._steps = tuple(steps)
        self._instrumentation = instrumentation

    def run(self, *, metrics: BacktestMetrics) -> BacktestContext:
        """Create a fresh run context and return the last Step's context.

        Example:
            result = pipeline.run(metrics=metrics)
            portfolio = result.metrics.portfolio
        """
        context = BacktestContext(metrics=metrics)
        with self._instrumentation:
            return run_steps(context=context, steps=self._steps, instrumentation=self._instrumentation)
