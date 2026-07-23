# filepath: src/training/pipeline.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.observability.instrumentation import Instrumentation, NoOpInstrumentation
from src.pipeline.step import PipelineStep
from src.training.context import TrainingContext


@dataclass(frozen=True)
class TrainingScheduleEntry:
    train_start_date: str
    train_end_date: str
    eval_start_date: str = ""
    eval_end_date: str = ""


class TrainingPipeline:
    """
    Orchestrates one formal training experiment over configured partitions.

    The pipeline owns schedule iteration and execution order only. Workflow
    construction owns execution identity, config injection, and path access.
    """

    def __init__(
            self,
            *,
            schedule: Sequence[TrainingScheduleEntry],
            daily_steps: Sequence[PipelineStep[TrainingContext]],
            final_steps: Sequence[PipelineStep[TrainingContext]],
            inst: Instrumentation | NoOpInstrumentation | None = None,
    ) -> None:
        self.schedule = tuple(schedule)
        self.daily_steps = tuple(daily_steps)
        self.final_steps = tuple(final_steps)
        self.inst = inst if inst is not None else NoOpInstrumentation()

    def run(self, ctx: TrainingContext) -> TrainingContext:
        for entry in self.schedule:
            ctx.train_start_date = entry.train_start_date
            ctx.train_end_date = entry.train_end_date
            ctx.eval_start_date = entry.eval_start_date
            ctx.eval_end_date = entry.eval_end_date
            ctx.trade_date = entry.train_end_date
            ctx.eval_date = entry.eval_end_date
            ctx.eval_pred = None

            for step in self.daily_steps:
                with self.inst.timer(step.__class__.__name__):
                    next_ctx = step.run(ctx)
                if next_ctx is None:
                    raise RuntimeError(
                        f"training step returned no context: {step.__class__.__name__}"
                    )
                ctx = next_ctx

        for step in self.final_steps:
            with self.inst.timer(step.__class__.__name__):
                next_ctx = step.run(ctx)
            if next_ctx is None:
                raise RuntimeError(
                    f"training step returned no context: {step.__class__.__name__}"
                )
            ctx = next_ctx

        self.inst.generate_timeline_report(ctx.experiment_name)

        return ctx
