# filepath: src/training/steps/model_train.py
"""Train one fresh model from a prepared training Context."""

from __future__ import annotations

from collections.abc import Mapping

from src.training.context import TrainingContext
from src.training.inference_model import InferenceModel
from src.training.models.catalog import ModelTrainer


class ModelTrainStep:
    """Train and attach one fresh configured model per Context window.

    Example:
        step = ModelTrainStep(
            trainer=get_model_trainer("sgd_regression"),
            model_params={"random_state": 7},
            feature_set="daily",
            feature_version="v1",
        )
        trained_context = step.run(preprocessed_context)
    """

    def __init__(
        self,
        *,
        trainer: ModelTrainer,
        model_params: Mapping[str, object],
        feature_set: str,
        feature_version: str,
    ) -> None:
        """Bind one catalog-selected trainer and its parameters.

        Example:
            step = ModelTrainStep(
                trainer=get_model_trainer("sgd_regression"),
                model_params={"random_state": 7},
                feature_set="daily",
                feature_version="v1",
            )
        """
        self._trainer = trainer
        self._model_params = dict(model_params)
        self._feature_set = feature_set
        self._feature_version = feature_version

    def run(self, context: TrainingContext) -> TrainingContext:
        """Train from the prepared Context partition and attach the model.

        Example:
            trained_context = step.run(preprocessed_context)
        """
        if (
            context.train_X is None
            or context.train_y is None
            or context.preprocess is None
        ):
            raise RuntimeError(
                "ModelTrainStep requires preprocessed training data and fitted state"
            )
        raw_model = self._trainer(
            X=context.train_X,
            y=context.train_y,
            model_params=self._model_params,
        )
        context.model = InferenceModel(
            model=raw_model,
            preprocess=context.preprocess,
            feature_set=self._feature_set,
            feature_version=self._feature_version,
        )
        return context
