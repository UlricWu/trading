# filepath: src/data_system/pipeline.py
"""Execute one workflow-supplied offline data Step sequence."""

from __future__ import annotations

from collections.abc import Sequence

from src.data_system.context import DataContext
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps


class DataPipeline:
    """Run workflow-supplied data Steps once in their supplied order.

    Example:
        pipeline = DataPipeline(
            steps=(calendar_step, fact_step),
            instrumentation=Instrumentation("data-level2_2026-07"),
        )
        result = pipeline.run(
            DataContext(start="2026-07-01", end="2026-07-20")
        )
    """

    def __init__(
        self,
        *,
        steps: Sequence[PipelineStep[DataContext]],
        instrumentation: Instrumentation,
    ) -> None:
        """Preserve the workflow-supplied Step sequence.

        Example:
            pipeline = DataPipeline(
                steps=(calendar_step, fact_step),
                instrumentation=Instrumentation("data-level2_2026-07"),
            )
        """
        self._steps = tuple(steps)
        self._instrumentation = instrumentation

    def run(self, context: DataContext) -> DataContext:
        """Execute every Step exactly once in its supplied order.

        Example:
            result = pipeline.run(
                DataContext(start="2026-07-01", end="2026-07-20")
            )
        """
        with self._instrumentation:
            return run_steps(
                context=context,
                steps=self._steps,
                instrumentation=self._instrumentation,
            )
