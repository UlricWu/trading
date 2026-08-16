# filepath: src/training/steps/artifact_persist_step.py
"""Persist the final explicit outputs of an offline training workflow."""

from __future__ import annotations

import json
from collections.abc import Mapping

import joblib

from src import logs
from src.config.model_config import FeatureLabelConfig
from src.training.artifact import PreprocessArtifact
from src.training.context import TrainingContext
from src.training.inference_model import PredictionModel
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


def persist_training_artifacts(
    *,
    pm: PathManager,
    experiment_name: str,
    experiment_id: str,
    model: PredictionModel,
    preprocess: PreprocessArtifact,
    metrics: Mapping[str, float],
    model_group: str,
    dataset_cfg: FeatureLabelConfig,
    label_lookahead: int,
    asof_day: str,
    processed_version: str,
) -> None:
    """Persist the final model, preprocessing, parameters, and metrics.

    Example:
        persist_training_artifacts(
            pm=path_manager,
            experiment_name="training_2026-07-01_2026-07-20_run-1",
            experiment_id="run-1",
            model=model,
            preprocess=preprocess_artifact,
            metrics={"ic@2026-07-20": 0.1},
            model_group="sgd_regression",
            dataset_cfg=model_config.dataset,
            label_lookahead=2,
            asof_day="2026-07-19",
            processed_version="v1",
        )
    """
    artifact_dir = pm.experiment_training_dir(experiment_name=experiment_name)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        model,
        pm.experiment_training_model(experiment_name=experiment_name),
    )
    joblib.dump(
        preprocess,
        pm.experiment_training_preprocess(experiment_name=experiment_name),
    )
    logs.info(
        f"type=model path={artifact_dir} "
        f"model_group={model_group} asof_day={asof_day}"
    )

    adjustment = dataset_cfg.adjustment
    params = {
        "experiment_id": experiment_id,
        "experiment_name": experiment_name,
        "model_group": model_group,
        "asof_day": asof_day,
        "created_at": DateTimeUtils.now().isoformat(),
        "feature_names": list(preprocess.feature_columns),
        "feature_set": dataset_cfg.feature_set,
        "feature_version": dataset_cfg.feature_version,
        "label_set": dataset_cfg.label_set,
        "label_version": dataset_cfg.label_version,
        "label_column": dataset_cfg.label_column,
        "label_lookahead": label_lookahead,
        "price_adjustment": adjustment.method,
        "adjustment_refdata": {
            "dataset_name": adjustment.dataset_name,
            "version": processed_version,
        },
    }
    pm.experiment_training_params(experiment_name=experiment_name).write_text(
        json.dumps(params, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    pm.experiment_training_metrics(experiment_name=experiment_name).write_text(
        json.dumps(dict(metrics), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


class ArtifactPersistStep:
    """Persist the final trained Context under one experiment identity.

    Example:
        step = ArtifactPersistStep(
            pm=path_manager,
            experiment_name="training_2026-07-01_2026-07-20_run-1",
            experiment_id="run-1",
            model_group="sgd_regression",
            dataset_cfg=model_config.dataset,
            label_lookahead=1,
            processed_version="v1",
        )
        persisted_context = step.run(evaluated_context)
    """

    def __init__(
        self,
        *,
        pm: PathManager,
        experiment_name: str,
        experiment_id: str,
        model_group: str,
        dataset_cfg: FeatureLabelConfig,
        label_lookahead: int,
        processed_version: str,
    ) -> None:
        """Bind the final artifact identity and dataset lineage.

        Example:
            step = ArtifactPersistStep(
                pm=path_manager,
                experiment_name="training_2026-07-01_2026-07-20_run-1",
                experiment_id="run-1",
                model_group="sgd_regression",
                dataset_cfg=model_config.dataset,
                label_lookahead=1,
                processed_version="v1",
            )
        """
        self._pm = pm
        self._experiment_name = experiment_name
        self._experiment_id = experiment_id
        self._model_group = model_group
        self._dataset_cfg = dataset_cfg
        self._label_lookahead = label_lookahead
        self._processed_version = processed_version

    def run(self, context: TrainingContext) -> TrainingContext:
        """Persist the Context model, preprocessing, metrics, and lineage.

        Example:
            persisted_context = step.run(evaluated_context)
        """
        if context.model is None or context.preprocess is None:
            raise RuntimeError(
                "ArtifactPersistStep requires a model and preprocessing artifact"
            )
        persist_training_artifacts(
            pm=self._pm,
            experiment_name=self._experiment_name,
            experiment_id=self._experiment_id,
            model=context.model,
            preprocess=context.preprocess,
            metrics=context.metrics,
            model_group=self._model_group,
            dataset_cfg=self._dataset_cfg,
            label_lookahead=self._label_lookahead,
            asof_day=context.window.train_dates[-1],
            processed_version=self._processed_version,
        )
        return context
