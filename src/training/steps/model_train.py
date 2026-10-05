# filepath: src/training/steps/model_train.py
"""Train one fresh model from a prepared training Context."""

from __future__ import annotations

from collections.abc import Mapping

from src.config.model_config import FeatureLabelConfig
from src.training.context import TrainingContext
from src.training.inference_model import InferenceModel
from src.training.models.catalog import ModelTrainer


class ModelTrainStep:
    """Train and attach one fresh configured model per Context window.

    Example:
        step = ModelTrainStep(
            trainer=get_model_trainer("sgd_regression"),
            model_params={"random_state": 7},
            dataset_cfg=model_config.dataset,
        )
        trained_context = step.run(preprocessed_context)
    """

    def __init__(
        self,
        *,
        trainer: ModelTrainer,
        model_params: Mapping[str, object],
        dataset_cfg: FeatureLabelConfig,
    ) -> None:
        """Bind one catalog-selected trainer and its parameters.

        Example:
            step = ModelTrainStep(
                trainer=get_model_trainer("sgd_regression"),
                model_params={"random_state": 7},
                dataset_cfg=model_config.dataset,
            )
        """
        self._trainer = trainer
        self._model_params = dict(model_params)
        self._dataset_cfg = dataset_cfg

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
            feature_set=self._dataset_cfg.feature_set,
            feature_version=self._dataset_cfg.feature_version,
            train_dates=context.window.train_dates,
            label_set=self._dataset_cfg.label_set,
            label_version=self._dataset_cfg.label_version,
            label_column=self._dataset_cfg.label_column,
        )
        return context
