# filepath: src/training/engines/preprocessing.py
"""Own fitted missing-value state and the only feature transform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

import numpy as np
import pandas as pd

from src.config.model_config import PreprocessingConfig
from src.utils import table_ops


@dataclass(frozen=True, slots=True)
class FittedPreprocessor:
    """Contain fitted missing-value state and transform feature rows.

    Example:
        preprocessor = FittedPreprocessor.fit(
            train_X=pd.DataFrame({"factor": [1.0, float("nan")]}),
            config=PreprocessingConfig(),
        )
        keep_rows, values = preprocessor.transform(
            np.array([[float("nan")]], dtype=float)
        )
    """

    feature_names: tuple[str, ...]
    missing_method: Literal["constant", "mean", "median", "drop"]
    fill_values: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        names = tuple(self.feature_names)
        if not names or any(not isinstance(name, str) or not name for name in names):
            raise ValueError("feature_names must contain non-empty strings")
        if len(names) != len(set(names)):
            raise ValueError("feature_names must be unique")
        if self.missing_method not in {"constant", "mean", "median", "drop"}:
            raise ValueError(f"unsupported missing_method: {self.missing_method!r}")

        fill_values = tuple(float(value) for value in self.fill_values)
        expected_fill_count = 0 if self.missing_method == "drop" else len(names)
        if len(fill_values) != expected_fill_count:
            raise ValueError(
                "fill_values count must be zero for drop and match feature_names "
                "otherwise"
            )
        if not np.isfinite(np.asarray(fill_values, dtype=float)).all():
            raise ValueError("fill_values must be finite")
        object.__setattr__(self, "feature_names", names)
        object.__setattr__(self, "fill_values", fill_values)

    @classmethod
    def fit(
        cls,
        *,
        train_X: pd.DataFrame,
        config: PreprocessingConfig,
    ) -> Self:
        """Fit missing-value state from the actual ordered training columns.

        Example:
            preprocessor = FittedPreprocessor.fit(
                train_X=pd.DataFrame({"factor": [1.0, 3.0, float("nan")]}),
                config=PreprocessingConfig(
                    missing=MissingConfig(method="mean")
                ),
            )
        """
        table_ops.require_nonempty(train_X, who="preprocessing train_X")
        feature_names = tuple(train_X.columns)
        if any(not isinstance(name, str) or not name for name in feature_names):
            raise ValueError("preprocessing feature columns must be non-empty strings")
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("preprocessing feature columns must be unique")

        values = train_X.to_numpy(dtype=float, na_value=np.nan, copy=True)
        if np.isinf(values).any():
            raise ValueError("preprocessing input must not contain infinity")

        method = config.missing.method
        if method == "drop":
            return cls(feature_names=feature_names, missing_method=method)
        if method == "constant":
            fill_value = config.missing.fill_value
            if fill_value is None:
                raise ValueError("missing.fill_value is required for constant fill")
            fill_values = np.full(values.shape[1], fill_value, dtype=float)
        else:
            all_missing = np.isnan(values).all(axis=0)
            if all_missing.any():
                columns = [
                    feature_names[index]
                    for index in np.flatnonzero(all_missing).tolist()
                ]
                raise ValueError(
                    f"missing.method={method!r} cannot fit all-missing columns: "
                    f"{columns}"
                )
            fill_values = (
                np.nanmean(values, axis=0)
                if method == "mean"
                else np.nanmedian(values, axis=0)
            )
        return cls(
            feature_names=feature_names,
            missing_method=method,
            fill_values=tuple(float(value) for value in fill_values),
        )

    def transform(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return retained-row identity and transformed retained rows.

        Example:
            keep_rows, transformed = preprocessor.transform(
                np.array([[1.0], [float("nan")]], dtype=float)
            )
        """
        raw = np.asarray(values)
        if raw.ndim != 2 or raw.shape[1] != len(self.feature_names):
            raise ValueError(
                "preprocessing input shape must be "
                f"(*, {len(self.feature_names)}); got={raw.shape}"
            )
        transformed = raw.astype(float, copy=True)
        if np.isinf(transformed).any():
            raise ValueError("preprocessing input must not contain infinity")

        if self.missing_method == "drop":
            keep_rows = ~np.isnan(transformed).any(axis=1)
            return keep_rows, transformed[keep_rows]

        keep_rows = np.ones(transformed.shape[0], dtype=bool)
        missing_rows, missing_columns = np.where(np.isnan(transformed))
        if missing_rows.size:
            fill_values = np.asarray(self.fill_values, dtype=float)
            transformed[missing_rows, missing_columns] = fill_values[missing_columns]
        return keep_rows, transformed
