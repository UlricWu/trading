# filepath: tests/training/engines/test_preprocessing.py
"""Tests for the single fitted preprocessing operation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.config.model_config import MissingConfig, PreprocessingConfig
from src.training.engines.preprocessing import FittedPreprocessor


@pytest.mark.parametrize(
    ("method", "expected_fill"),
    [("mean", 2.0), ("median", 2.0)],
)
def test_fitted_statistics_are_reused_by_transform(
    method: str,
    expected_fill: float,
) -> None:
    preprocessor = FittedPreprocessor.fit(
        train_X=pd.DataFrame({"factor": [1.0, 3.0, np.nan]}),
        config=PreprocessingConfig(missing=MissingConfig(method=method)),  # type: ignore[arg-type]
    )

    keep_rows, transformed = preprocessor.transform(
        np.array([[np.nan], [5.0]], dtype=float)
    )

    assert keep_rows.tolist() == [True, True]
    assert transformed.tolist() == [[expected_fill], [5.0]]
    assert preprocessor.feature_names == ("factor",)


def test_drop_returns_the_identity_of_every_row_without_nan() -> None:
    preprocessor = FittedPreprocessor.fit(
        train_X=pd.DataFrame({"left": [1.0, 2.0], "right": [3.0, 4.0]}),
        config=PreprocessingConfig(missing=MissingConfig(method="drop")),
    )

    keep_rows, transformed = preprocessor.transform(
        np.array([[1.0, np.nan], [np.nan, 2.0], [3.0, 4.0]])
    )

    assert keep_rows.tolist() == [False, False, True]
    assert transformed.tolist() == [[3.0, 4.0]]


@pytest.mark.parametrize("method", ["mean", "median"])
def test_statistical_fit_rejects_all_missing_columns(method: str) -> None:
    with pytest.raises(ValueError, match="all-missing columns"):
        FittedPreprocessor.fit(
            train_X=pd.DataFrame({"factor": [np.nan, np.nan]}),
            config=PreprocessingConfig(
                missing=MissingConfig(method=method)  # type: ignore[arg-type]
            ),
        )


def test_explicit_constant_can_fill_an_all_missing_column() -> None:
    preprocessor = FittedPreprocessor.fit(
        train_X=pd.DataFrame({"factor": [np.nan, np.nan]}),
        config=PreprocessingConfig(
            missing=MissingConfig(method="constant", fill_value=7.0)
        ),
    )

    _, transformed = preprocessor.transform(np.array([[np.nan]]))

    assert transformed.tolist() == [[7.0]]


def test_transform_rejects_infinity_instead_of_treating_it_as_missing() -> None:
    preprocessor = FittedPreprocessor(
        feature_names=("factor",),
        missing_method="constant",
        fill_values=(0.0,),
    )

    with pytest.raises(ValueError, match="infinity"):
        preprocessor.transform(np.array([[np.inf]]))
