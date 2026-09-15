# filepath: src/training/inference_model.py
"""Define the ready-to-use prediction object shared by every runtime."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from src.training.engines.preprocessing import FittedPreprocessor


@runtime_checkable
class PredictionModel(Protocol):
    """Expose the raw model operation needed by fitted inference.

    Example:
        model: PredictionModel = SGDRegressor().fit(
            np.array([[0.0], [1.0]]),
            np.array([0.0, 1.0]),
        )
        predictions = model.predict(np.array([[0.5]]))
    """

    def predict(self, values: np.ndarray) -> np.ndarray | Sequence[float]:
        """Predict one value for each transformed row.

        Example:
            predictions = model.predict(np.array([[0.5]], dtype=float))
        """
        ...


@dataclass(frozen=True, slots=True)
class InferenceModel:
    """Own one raw model and its fitted preprocessing operation.

    Example:
        preprocessor = FittedPreprocessor(
            feature_names=("factor",),
            missing_method="constant",
            fill_values=(0.0,),
        )
        inference_model = InferenceModel(
            model=SGDRegressor().fit(
                np.array([[0.0], [1.0]]),
                np.array([0.0, 1.0]),
            ),
            preprocess=preprocessor,
            feature_set="daily",
            feature_version="v1",
        )
        keep_rows, predictions = inference_model.predict(
            np.array([[float("nan")]], dtype=float)
        )
    """

    model: PredictionModel
    preprocess: FittedPreprocessor
    feature_set: str
    feature_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.model, PredictionModel):
            raise TypeError("model must expose predict(values)")
        if not isinstance(self.preprocess, FittedPreprocessor):
            raise TypeError("preprocess must be a FittedPreprocessor")
        if not isinstance(self.feature_set, str) or not self.feature_set:
            raise ValueError("feature_set must be a non-empty string")
        if not isinstance(self.feature_version, str) or not self.feature_version:
            raise ValueError("feature_version must be a non-empty string")

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the actual ordered columns fitted during training.

        Example:
            names = inference_model.feature_names
        """
        return self.preprocess.feature_names

    def predict(self, raw_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Transform raw rows once and predict only retained rows.

        Example:
            keep_rows, predictions = inference_model.predict(
                np.array([[1.0], [float("nan")]], dtype=float)
            )
        """
        keep_rows, transformed = self.preprocess.transform(raw_values)
        if transformed.shape[0] == 0:
            return keep_rows, np.empty(0, dtype=float)

        predictions = np.asarray(self.model.predict(transformed), dtype=float)
        if predictions.ndim != 1 or predictions.shape[0] != transformed.shape[0]:
            raise RuntimeError(
                "model predictions must be one-dimensional and match retained rows"
            )
        if not np.isfinite(predictions).all():
            raise RuntimeError("model predictions must be finite")
        return keep_rows, predictions
