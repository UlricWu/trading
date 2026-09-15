# filepath: tests/training/steps/test_artifact_persist.py
"""Lineage projection tests for final training artifact publication."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from src.config.model_config import FeatureLabelConfig
from src.training.artifact import (
    load_inference_model,
    load_training_report_inputs,
)
from src.training.context import TrainingContext, TrainingWindow
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.training.steps.artifact_persist import ArtifactPersistStep
from src.utils.path import PathManager


class _FirstColumnModel:
    def predict(self, values: np.ndarray) -> np.ndarray:
        return values[:, 0]


def test_step_projects_fitted_feature_identity_into_params(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    experiment_name = "training_2026-07-01_2026-07-20_run-1"
    context = TrainingContext(
        window=TrainingWindow(train_dates=("2026-07-19",), eval_date="2026-07-20"),
        metrics={"ic@2026-07-20": 0.1},
        model=InferenceModel(
            model=_FirstColumnModel(),
            preprocess=FittedPreprocessor(
                feature_names=("actual_factor",),
                missing_method="constant",
                fill_values=(0.0,),
            ),
            feature_set="daily",
            feature_version="v1",
        ),
    )
    step = ArtifactPersistStep(
        pm=pm,
        experiment_name=experiment_name,
        experiment_id="run-1",
        model_group="sgd_regression",
        dataset_cfg=FeatureLabelConfig(
            feature_set="daily",
            feature_version="v1",
            feature_columns=["configured_factor"],
            label_set="rank",
            label_version="v1",
            label_column="target",
        ),
        label_lookahead=1,
    )

    step.run(context)

    params, _ = load_training_report_inputs(
        pm=pm,
        experiment_name=experiment_name,
    )
    loaded_model = load_inference_model(pm=pm, experiment_name=experiment_name)
    assert params.feature_names == ("actual_factor",)
    assert loaded_model.feature_names == ("actual_factor",)
