# filepath: tests/training/steps/test_model_train.py
"""Ready-model construction tests for the training Step."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from src.config.model_config import FeatureLabelConfig

import numpy as np
import pandas as pd

from src.training.context import TrainingContext, TrainingWindow
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.training.steps.model_train import ModelTrainStep


class _FirstColumnModel:
    def predict(self, values: np.ndarray) -> np.ndarray:
        return values[:, 0]


def _train_model(
    *,
    X: pd.DataFrame,
    y: pd.Series,
    model_params: Mapping[str, object],
) -> _FirstColumnModel:
    assert X.shape == (2, 1)
    assert y.tolist() == [1.0, 2.0]
    assert model_params == {"seed": 7}
    return _FirstColumnModel()


def test_model_train_attaches_an_inference_ready_model() -> None:
    fitted = FittedPreprocessor(
        feature_names=("factor",),
        missing_method="constant",
        fill_values=(0.0,),
    )
    context = TrainingContext(
        window=TrainingWindow(train_dates=("2026-07-01", "2026-07-02"), model_ref=Path("/tmp/model.joblib")),
        train_X=pd.DataFrame({"factor": [1.0, 2.0]}),
        train_y=pd.Series([1.0, 2.0]),
        preprocess=fitted,
    )

    ModelTrainStep(
        trainer=_train_model,
        model_params={"seed": 7},
        dataset_cfg=FeatureLabelConfig(
            feature_set="daily", feature_version="v1", feature_columns=["factor"],
            label_set="rank", label_version="v1", label_column="target",
        ),
    ).run(context)

    assert isinstance(context.model, InferenceModel)
    assert context.model.preprocess is fitted
    assert context.model.train_dates == context.window.train_dates
    assert (context.model.label_set, context.model.label_version, context.model.label_column) == ("rank", "v1", "target")
    predictions = context.model.predict(np.array([[np.nan], [3.0]]))
    assert predictions.tolist() == [0.0, 3.0]
