# filepath: src/training/pipeline.py
"""Schedule workflow-supplied Steps over offline training windows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.training.context import TrainingContext, TrainingWindow


class TrainingPipeline:
    """Run ordered Step tuples over an already resolved training schedule.

    Example:
        pipeline = TrainingPipeline(
            windows=windows,
            per_window_steps=(dataset, preprocess, train, evaluate),
            final_steps=(persist, report),
            instrumentation=Instrumentation("training_2026-07_run-1"),
        )
        pipeline.run()
    """

    def __init__(
        self,
        *,
        windows: Sequence[TrainingWindow],
        per_window_steps: Sequence[PipelineStep[TrainingContext]],
        final_steps: Sequence[PipelineStep[TrainingContext]],
        instrumentation: Instrumentation,
    ) -> None:
        """Preserve the workflow-supplied schedule and Step order.

        Example:
            pipeline = TrainingPipeline(
                windows=windows,
                per_window_steps=(dataset, preprocess, train, evaluate),
                final_steps=(persist, report),
                instrumentation=Instrumentation("training_2026-07_run-1"),
            )
        """
        self._windows = tuple(windows)
        self._per_window_steps = tuple(per_window_steps)
        self._final_steps = tuple(final_steps)
        self._instrumentation = instrumentation

    def run(self) -> None:
        """Execute every window and then the final Steps in supplied order.

        Example:
            pipeline.run()
        """
        if not self._windows:
            raise ValueError("[TrainingSchedule] empty schedule")

        metrics: dict[str, float] = {}
        last_context: TrainingContext | None = None
        with self._instrumentation:
            for window in self._windows:
                last_context = run_steps(
                    context=TrainingContext(window=window, metrics=metrics),
                    steps=self._per_window_steps,
                    instrumentation=self._instrumentation,
                )

            final_context = cast(TrainingContext, last_context)
            run_steps(
                context=final_context,
                steps=self._final_steps,
                instrumentation=self._instrumentation,
            )
