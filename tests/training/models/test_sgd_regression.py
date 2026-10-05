# filepath: tests/training/models/test_sgd_regression.py
"""Fresh-model and input-boundary tests for SGD training."""

from __future__ import annotations

import numpy as np
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
    np.testing.assert_array_equal(first.coef_, second.coef_, strict=True)
    np.testing.assert_array_equal(first.intercept_, second.intercept_, strict=True)
    assert first.t_ == second.t_
    assert first.n_iter_ == 1
    assert second.n_iter_ == 1


def test_train_rejects_misaligned_input_lengths() -> None:
    with pytest.raises(ValueError, match="input length mismatch"):
        train_sgd_regression(
            X=pd.DataFrame({"factor": [0.0, 1.0]}),
            y=pd.Series([0.0]),
            model_params={},
        )


@pytest.mark.parametrize("params", [{}, {"random_state": None}, {"random_state": True}])
def test_train_requires_an_explicit_integer_seed(params: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="random_state"):
        train_sgd_regression(
            X=pd.DataFrame({"factor": [0.0, 1.0]}),
            y=pd.Series([0.0, 1.0]),
            model_params=params,
        )


def test_seed_makes_training_independent_of_ambient_random_state() -> None:
    values = np.linspace(-1.0, 1.0, 32)
    features = pd.DataFrame({"factor": values})
    labels = pd.Series(values * values + 0.3 * values)
    original_state = np.random.get_state()
    try:
        np.random.seed(0)
        first = train_sgd_regression(
            X=features, y=labels,
            model_params={"alpha": 0.0005, "l1_ratio": 0, "random_state": 7},
        )
        np.random.seed(1)
        second = train_sgd_regression(
            X=features, y=labels,
            model_params={"alpha": 0.0005, "l1_ratio": 0, "random_state": 7},
        )
    finally:
        np.random.set_state(original_state)
    np.testing.assert_array_equal(first.coef_, second.coef_, strict=True)
    np.testing.assert_array_equal(first.intercept_, second.intercept_, strict=True)
    np.testing.assert_array_equal(
        first.predict(features.to_numpy()), second.predict(features.to_numpy()), strict=True,
    )
