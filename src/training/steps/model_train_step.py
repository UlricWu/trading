# filepath: src/training/steps/model_train_step.py
from __future__ import annotations

from collections.abc import Mapping

from src.utils import table_ops
from src.training.context import TrainingContext
from src.training.engines.model.sgd_regressor_train_engine import (
    SklearnSGDRegressorTrainEngine,
)


class ModelTrainStep:
    """
    Train a fresh model from the current in-memory train partition.

    The step consumes `ctx.train_X` and `ctx.train_y`; it overwrites any prior
    `ctx.model_state` and never reads feature/label files directly.

    Example:
        step = ModelTrainStep(group="sgd_regression")
        step(context)
    """

    def __init__(
        self,
        *,
        group: str,
        model_params: Mapping[str, object] | None = None,
    ) -> None:
        """Create the step with workflow-injected model parameters.

        Example:
            step = ModelTrainStep(group="sgd_regression")
        """
        self.group = group
        self.model_params = dict(model_params or {})

        if group == "sgd_regression":
            self.engine = SklearnSGDRegressorTrainEngine(model_params=self.model_params)
        else:
            raise ValueError(f"unsupported model train group: {group}")

    def __call__(self, ctx: TrainingContext) -> None:
        """Train the in-memory model for the current schedule entry.

        Example:
            step(context)
        """

        table_ops.require_nonempty(ctx.train_X, who="ModelTrainStep train_X")
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
