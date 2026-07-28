# filepath: src/training/engines/preprocess_engine.py
from __future__ import annotations

import pandas as pd

from src.config.model_config import MissingConfig, PreprocessingConfig
from src.training.artifact import PreprocessArtifact


class PreprocessEngine:
    """
    In-memory preprocessing engine for missing-value handling.

    The engine learns fill values from train_X only. Eval data is transformed
    with the resulting artifact; no values are estimated from eval data.
    """

    def fit_transform(
        self,
        *,
        train_X: pd.DataFrame,
        cfg: PreprocessingConfig,
    ) -> tuple[pd.DataFrame, PreprocessArtifact]:
        X, fill_values = _fit_missing(train_X.copy(), cfg.missing)

        artifact = PreprocessArtifact(
            feature_columns=X.columns.tolist(),
            missing_method=cfg.missing.method,
            fill_values=fill_values,
        )
        return X, artifact

    def transform_with_artifact(
        self,
        *,
        X: pd.DataFrame,
        artifact: PreprocessArtifact,
    ) -> pd.DataFrame:
        out = X.reindex(columns=artifact.feature_columns).copy()
        return _apply_missing(out, artifact)


def _fit_missing(
    X: pd.DataFrame,
    cfg: MissingConfig,
) -> tuple[pd.DataFrame, dict[str, float]]:
    method = cfg.method

    if method == "drop":
        return X.dropna(axis=0, how="any"), {}

    fill_values = _fill_values(X, cfg)
    return X.fillna(value=fill_values), fill_values


def _apply_missing(X: pd.DataFrame, artifact: PreprocessArtifact) -> pd.DataFrame:
    if artifact.missing_method == "drop":
        return X.dropna(axis=0, how="any")

    # pandas accepts concrete dict/Series fill values rather than the general
    # read-only Mapping exposed by the artifact.
    out = X.fillna(value=dict(artifact.fill_values))
    if out.isna().any().any():
        missing = out.columns[out.isna().any()].tolist()
        raise ValueError(
            "[PreprocessEngine] missing values remain without artifact fill values: "
            f"{missing}"
        )
    return out


def _fill_values(X: pd.DataFrame, cfg: MissingConfig) -> dict[str, float]:
    if any(not isinstance(column, str) for column in X.columns):
        raise ValueError("preprocessing feature columns must be strings")
    method = cfg.method

    if method == "constant":
        if cfg.fill_value is None:
            raise ValueError("missing.fill_value is required for constant fill")
        return {col: float(cfg.fill_value) for col in X.columns}

    numeric_cols = X.select_dtypes(include=["number"]).columns
    numeric_col_set = set(numeric_cols)
    non_numeric_missing = [
        col for col in X.columns[X.isna().any()]
        if col not in numeric_col_set
    ]
    if non_numeric_missing:
        raise ValueError(
            f"missing.method={method!r} requires numeric missing columns; "
            f"non_numeric_missing={non_numeric_missing}"
        )

    if method == "mean":
        values = X[numeric_cols].mean(axis=0)
    elif method == "median":
        values = X[numeric_cols].median(axis=0)
    else:
        raise ValueError(f"Unknown missing.method: {method}")

    values = values.fillna(0.0)
    return {col: float(value) for col, value in values.items()}
