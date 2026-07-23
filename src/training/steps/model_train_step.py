# filepath: src/training/steps/model_train_step.py
from __future__ import annotations

from collections.abc import Mapping

from src.pipeline.step import PipelineStep
from src.training.context import TrainingContext
from src.training.engines.model.sgd_regressor_train_engine import (
    SklearnSGDRegressorTrainEngine,
)


class ModelTrainStep(PipelineStep[TrainingContext]):
    """
    Train a fresh model from the current in-memory train partition.

    The step consumes `ctx.train_X` and `ctx.train_y`; it overwrites any prior
    `ctx.model_state` and never reads feature/label files directly.
    """

    def __init__(
        self,
        *,
        group: str,
        model_params: Mapping[str, object] | None = None,
    ) -> None:
        """Create the step with the workflow-injected model parameter fragment."""
        super().__init__()
        self.group = group
        self.model_params = dict(model_params or {})

        if group == "sgd_regression":
            self.engine = SklearnSGDRegressorTrainEngine(model_params=self.model_params)
        else:
            raise ValueError(f"unsupported model train group: {group}")

    def run(self, ctx: TrainingContext) -> TrainingContext:
        """Train the in-memory model state for the current schedule entry."""

        if ctx.train_X is None or ctx.train_y is None:
            raise RuntimeError("[ModelTrainStep] train_X / train_y not set")
        if ctx.train_X.empty:
            raise RuntimeError("[ModelTrainStep] train_X is empty")
        if len(ctx.train_X) != len(ctx.train_y):
            raise RuntimeError(
                f"[ModelTrainStep] train_X / train_y length mismatch: "
                f"X={len(ctx.train_X)} y={len(ctx.train_y)}"
            )
        ctx.model_state = self.engine.train(
            X=ctx.train_X,
            y=ctx.train_y,
            asof_day=ctx.train_end_date,
        )
        return ctx
