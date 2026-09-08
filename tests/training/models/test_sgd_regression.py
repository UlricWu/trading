# filepath: tests/training/models/test_sgd_regression.py
"""Fresh-model and input-boundary tests for SGD training."""

from __future__ import annotations

import pandas as pd
import pytest

from src.training.models.sgd_regression import train_sgd_regression


def test_each_train_call_returns_a_fresh_model() -> None:
    features = pd.DataFrame({"factor": [0.0, 1.0]})
    labels = pd.Series([0.0, 1.0])

    first = train_sgd_regression(
        X=features,
        y=labels,
        model_params={"random_state": 7},
    )
    second = train_sgd_regression(
        X=features,
        y=labels,
        model_params={"random_state": 7},
    )

    assert first is not second
    assert first.n_iter_ == 1
    assert second.n_iter_ == 1


def test_train_rejects_misaligned_input_lengths() -> None:
    with pytest.raises(ValueError, match="input length mismatch"):
        train_sgd_regression(
            X=pd.DataFrame({"factor": [0.0, 1.0]}),
            y=pd.Series([0.0]),
            model_params={},
        )
