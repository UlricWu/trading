# filepath: src/training/steps/model_train_step.py
"""Train one fresh model from a prepared training Context."""

from __future__ import annotations

from collections.abc import Mapping

from src.training.context import TrainingContext
from src.training.engines.model.sgd_regressor_train_engine import (
    SklearnSGDRegressorTrainEngine,
)


class ModelTrainStep:
    """Train and attach one fresh SGD regression model per Context window.

    Example:
        step = ModelTrainStep(model_params={"random_state": 7})
        trained_context = step.run(preprocessed_context)
    """

    def __init__(self, *, model_params: Mapping[str, object]) -> None:
        """Bind the fixed SGD trainer configuration.

        Example:
            step = ModelTrainStep(model_params={"random_state": 7})
        """
        self._engine = SklearnSGDRegressorTrainEngine(model_params=dict(model_params))

    def run(self, context: TrainingContext) -> TrainingContext:
        """Train from the prepared Context partition and attach the model.

        Example:
            trained_context = step.run(preprocessed_context)
        """
        if context.train_X is None or context.train_y is None:
            raise RuntimeError("ModelTrainStep requires preprocessed training data")
        context.model = self._engine.train(
            X=context.train_X,
            y=context.train_y,
        )
        return context
