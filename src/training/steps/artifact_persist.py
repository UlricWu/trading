# filepath: src/training/steps/artifact_persist.py
"""Publish the final ready inference asset and its report inputs."""

from __future__ import annotations

from src import logs
from src.config.model_config import FeatureLabelConfig
from src.training.artifact import TrainingParams, persist_training_artifacts
from src.training.context import TrainingContext
from src.utils.path import PathManager


class ArtifactPersistStep:
    """Publish the final trained Context under one experiment identity.

    Example:
        step = ArtifactPersistStep(
            pm=path_manager,
            experiment_name="training_2026-07-01_2026-07-20_run-1",
            experiment_id="run-1",
            model_group="sgd_regression",
            dataset_cfg=model_config.dataset,
            label_lookahead=1,
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
    ) -> None:
        """Bind final artifact identity and feature/label lineage.

        Example:
            step = ArtifactPersistStep(
                pm=path_manager,
                experiment_name="training_2026-07-01_2026-07-20_run-1",
                experiment_id="run-1",
                model_group="sgd_regression",
                dataset_cfg=model_config.dataset,
                label_lookahead=1,
            )
        """
        self._pm = pm
        self._experiment_name = experiment_name
        self._experiment_id = experiment_id
        self._model_group = model_group
        self._dataset_cfg = dataset_cfg
        self._label_lookahead = label_lookahead

    def run(self, context: TrainingContext) -> TrainingContext:
        """Publish the Context inference model, params, and metrics.

        Example:
            persisted_context = step.run(evaluated_context)
        """
        if context.model is None:
            raise RuntimeError("ArtifactPersistStep requires an inference model")
        asof_day = context.window.train_dates[-1]
        params = TrainingParams(
            experiment_id=self._experiment_id,
            model_group=self._model_group,
            asof_day=asof_day,
            feature_set=context.model.feature_set,
            feature_version=context.model.feature_version,
            feature_names=context.model.feature_names,
            label_set=self._dataset_cfg.label_set,
            label_version=self._dataset_cfg.label_version,
            label_column=self._dataset_cfg.label_column,
            label_lookahead=self._label_lookahead,
        )
        persist_training_artifacts(
            pm=self._pm,
            experiment_name=self._experiment_name,
            params=params,
            metrics=context.metrics,
            inference_model=context.model,
        )
        logs.info(
            "type=training_inference "
            f"path={self._pm.experiment_training_inference(experiment_name=self._experiment_name)} "
            f"model_group={self._model_group} asof_day={asof_day}"
        )
        return context
