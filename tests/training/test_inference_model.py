# filepath: tests/training/test_inference_model.py
from dataclasses import dataclass

import numpy as np
import pytest

from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel


@dataclass
class Predictor:
    values: np.ndarray | None = None
    calls: int = 0

    def predict(self, values: np.ndarray) -> np.ndarray:
        self.calls += 1
        return values[:, 0] if self.values is None else self.values


def _make_model(predictor: Predictor, method: str = "drop") -> InferenceModel:
    return InferenceModel(
        model=predictor,
        preprocess=FittedPreprocessor(
            feature_names=("factor",), missing_method=method,
            fill_values=() if method == "drop" else (2.0,),
        ),
        feature_set="daily", feature_version="v1",
        train_dates=("2026-07-01", "2026-07-02"),
        label_set="rank", label_version="v1", label_column="target",
    )


def test_prediction_preserves_original_rows_zero_and_missing_positions() -> None:
    model = _make_model(Predictor())
    result = model.predict(np.array([[0.0], [np.nan], [2.0]]))
    np.testing.assert_array_equal(result, np.array([0.0, np.nan, 2.0]), strict=True)
    assert model.train_dates == ("2026-07-01", "2026-07-02")
    assert model.feature_names == ("factor",)


@pytest.mark.parametrize("values", [np.empty((0, 1)), np.array([[np.nan], [np.nan]])])
def test_no_retained_rows_do_not_call_predictor(values: np.ndarray) -> None:
    predictor = Predictor()
    result = _make_model(predictor).predict(values)
    np.testing.assert_array_equal(result, np.full(len(values), np.nan), strict=True)
    assert predictor.calls == 0


def test_filling_produces_prediction_for_every_row() -> None:
    result = _make_model(Predictor(), "constant").predict(np.array([[np.nan], [0.0]]))
    np.testing.assert_array_equal(result, np.array([2.0, 0.0]), strict=True)


@pytest.mark.parametrize("invalid", [np.array([[1.0]]), np.array([]), np.array([1.0, 2.0]), np.array([np.inf]), np.array([np.nan])])
def test_invalid_raw_predictions_fail_before_alignment(invalid: np.ndarray) -> None:
    with pytest.raises(RuntimeError):
        _make_model(Predictor(values=invalid)).predict(np.array([[1.0], [np.nan]]))


def test_infinite_input_is_an_error() -> None:
    with pytest.raises(ValueError, match="infinity"):
        _make_model(Predictor()).predict(np.array([[np.inf]]))
