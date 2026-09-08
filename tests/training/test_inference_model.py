# filepath: tests/training/test_inference_model.py
"""Tests for the ready inference object shared by all runtimes."""

from __future__ import annotations

import numpy as np

from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel


class _RecordingModel:
    def __init__(self) -> None:
        self.inputs: list[np.ndarray] = []

    def predict(self, values: np.ndarray) -> np.ndarray:
        self.inputs.append(values.copy())
        return values[:, 0]


def test_predict_uses_fitted_transform_and_returns_retained_row_identity() -> None:
    raw_model = _RecordingModel()
    inference_model = InferenceModel(
        model=raw_model,
        preprocess=FittedPreprocessor(
            feature_names=("factor",),
            missing_method="drop",
        ),
        feature_set="daily",
        feature_version="v1",
    )

    keep_rows, predictions = inference_model.predict(np.array([[1.0], [np.nan], [3.0]]))

    assert inference_model.feature_names == ("factor",)
    assert keep_rows.tolist() == [True, False, True]
    assert predictions.tolist() == [1.0, 3.0]
    assert [values.tolist() for values in raw_model.inputs] == [[[1.0], [3.0]]]


def test_predict_does_not_call_raw_model_when_drop_retains_no_rows() -> None:
    raw_model = _RecordingModel()
    inference_model = InferenceModel(
        model=raw_model,
        preprocess=FittedPreprocessor(
            feature_names=("factor",),
            missing_method="drop",
        ),
        feature_set="daily",
        feature_version="v1",
    )

    keep_rows, predictions = inference_model.predict(np.array([[np.nan]]))

    assert keep_rows.tolist() == [False]
    assert predictions.size == 0
    assert raw_model.inputs == []
