# filepath: src/training/inference_model.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class PredictionModel(Protocol):
    def predict(self, values: np.ndarray) -> np.ndarray | Sequence[float]: ...


@runtime_checkable
class Preprocessor(Protocol):
    feature_columns: Sequence[str]

    def transform(self, values: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class InferenceModel:
    """
    Pure inference engine (NO diagnostics)

    Responsibilities:
        - enforce preprocess
        - call model.predict
    """

    model: PredictionModel
    preprocess: Preprocessor
    feature_names: Sequence[str]
    feature_set: str
    feature_version: str
    label_lookahead: int

    def __post_init__(self) -> None:
        names = tuple(self.feature_names)
        if not names or any(
            not isinstance(name, str) or not name for name in names
        ):
            raise ValueError("feature_names must contain non-empty strings")
        if len(names) != len(set(names)):
            raise ValueError("feature_names must be unique")
        object.__setattr__(self, "feature_names", names)
        if not isinstance(self.feature_set, str) or not self.feature_set:
            raise ValueError("feature_set must be a non-empty string")
        if not isinstance(self.feature_version, str) or not self.feature_version:
            raise ValueError("feature_version must be a non-empty string")
        if (
            isinstance(self.label_lookahead, bool)
            or not isinstance(self.label_lookahead, int)
            or self.label_lookahead < 0
        ):
            raise ValueError("label_lookahead must be a non-negative int")

    def predict(self, X_raw: np.ndarray) -> np.ndarray:
        """
        Args:
            X_raw: shape (n_symbols, n_features)

        Returns:
            np.ndarray shape (n_symbols,)
        """
        if X_raw.ndim != 2 or X_raw.shape[1] != len(self.feature_names):
            raise ValueError(
                f"inference input shape must be (*, {len(self.feature_names)}); "
                f"got={X_raw.shape}"
            )
        if X_raw.shape[0] == 0:
            return np.empty(0, dtype=float)

        Xp = self.preprocess.transform(X_raw)
        predictions = np.asarray(self.model.predict(Xp), dtype=float)
        if predictions.ndim != 1 or predictions.shape[0] != X_raw.shape[0]:
            raise RuntimeError(
                "model predictions must be one-dimensional and preserve input rows"
            )
        if not np.isfinite(predictions).all():
            raise RuntimeError("model predictions must be finite")
        return predictions
