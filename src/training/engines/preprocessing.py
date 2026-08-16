# filepath: src/training/engines/preprocessing.py
"""Fit and apply train-owned missing-value preprocessing."""

from __future__ import annotations

import pandas as pd

from src.config.model_config import MissingConfig, PreprocessingConfig
from src.training.artifact import PreprocessArtifact
from src.utils import table_ops


def fit_preprocessing(
    *,
    train_X: pd.DataFrame,
    config: PreprocessingConfig,
) -> tuple[pd.DataFrame, PreprocessArtifact]:
    """Fit missing-value parameters on training features only.

    Example:
        transformed, artifact = fit_preprocessing(
            train_X=pd.DataFrame({"factor": [1.0, None]}),
            config=PreprocessingConfig(),
        )
    """
    transformed, fill_values = _fit_missing(train_X.copy(), config.missing)
    artifact = PreprocessArtifact(
        feature_columns=transformed.columns.tolist(),
        missing_method=config.missing.method,
        fill_values=fill_values,
    )
    return transformed, artifact


def apply_preprocessing(
    *,
    X: pd.DataFrame,
    artifact: PreprocessArtifact,
) -> pd.DataFrame:
    """Apply one fitted preprocessing artifact to feature rows.

    Example:
        transformed = apply_preprocessing(
            X=pd.DataFrame({"factor": [None]}),
            artifact=artifact,
        )
    """
    transformed = X.reindex(columns=list(artifact.feature_columns)).copy()
    return _apply_missing(transformed, artifact)


def _fit_missing(
    X: pd.DataFrame,
    config: MissingConfig,
) -> tuple[pd.DataFrame, dict[str, float]]:
    if config.method == "drop":
        return X.dropna(axis=0, how="any"), {}

    fill_values = _fill_values(X, config)
    return X.fillna(value=fill_values), fill_values


def _apply_missing(X: pd.DataFrame, artifact: PreprocessArtifact) -> pd.DataFrame:
    if artifact.missing_method == "drop":
        return X.dropna(axis=0, how="any")

    transformed = X.fillna(value=dict(artifact.fill_values))
    if len(transformed.columns) > 0:
        table_ops.require_non_null(
            transformed,
            tuple(transformed.columns),
            who="training preprocessing output",
        )
    return transformed


def _fill_values(X: pd.DataFrame, config: MissingConfig) -> dict[str, float]:
    if any(not isinstance(column, str) for column in X.columns):
        raise ValueError("preprocessing feature columns must be strings")

    if config.method == "constant":
        if config.fill_value is None:
            raise ValueError("missing.fill_value is required for constant fill")
        return {column: float(config.fill_value) for column in X.columns}

    numeric_columns = X.select_dtypes(include=["number"]).columns
    numeric_column_set = set(numeric_columns)
    non_numeric_missing = [
        column
        for column in X.columns[X.isna().any()]
        if column not in numeric_column_set
    ]
    if non_numeric_missing:
        raise ValueError(
            f"missing.method={config.method!r} requires numeric missing columns; "
            f"non_numeric_missing={non_numeric_missing}"
        )

    if config.method == "mean":
        values = X[numeric_columns].mean(axis=0)
    elif config.method == "median":
        values = X[numeric_columns].median(axis=0)
    else:
        raise ValueError(f"unknown missing.method: {config.method}")

    values = values.fillna(0.0)
    return {str(column): float(value) for column, value in values.items()}
