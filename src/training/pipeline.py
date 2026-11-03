# filepath: src/training/pipeline.py
"""Run one model's training and evaluation Steps with shared measurement."""

from __future__ import annotations

from collections.abc import Sequence

from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.training.context import TrainingContext, TrainingWindow


class TrainingPipeline:
    """Run the supplied Steps once over one resolved training selection.

    Example:
        pipeline = TrainingPipeline(
            window=window,
            steps=(dataset, preprocess, train, persist, predict, evaluate, metrics, report),
            instrumentation=Instrumentation("training_2026-07_run-1"),
        )
        pipeline.run()
    """

    def __init__(
        self,
        *,
        window: TrainingWindow,
        steps: Sequence[PipelineStep[TrainingContext]],
        instrumentation: Instrumentation,
    ) -> None:
        """Preserve one training selection and the workflow's Step order.

        Example:
            pipeline = TrainingPipeline(
                window=window,
                steps=(dataset, preprocess, train, persist, predict, evaluate, metrics, report),
                instrumentation=Instrumentation("training_2026-07_run-1"),
            )
        """
        self._window = window
        self._steps = tuple(steps)
        self._instrumentation = instrumentation

    def run(self) -> None:
        """Execute the chain once, preserving returned Contexts and observations.

        Example:
            pipeline.run()
        """
        with self._instrumentation:
            run_steps(
                context=TrainingContext(window=self._window),
                steps=self._steps,
                instrumentation=self._instrumentation,
            )
