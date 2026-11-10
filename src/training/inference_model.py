# filepath: src/training/inference_model.py
"""Define the ready-to-use prediction object shared by every runtime."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

import numpy as np
from threadpoolctl import threadpool_limits

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


@runtime_checkable
class ProbabilityModel(Protocol):
    """Expose class identities and their probabilities.

    Example:
        model: ProbabilityModel = LogisticRegression().fit(values, labels)
        probabilities = model.predict_proba(values)
    """

    classes_: np.ndarray

    def predict_proba(self, values: np.ndarray) -> np.ndarray:
        """Return probabilities in classes_ order.

        Example:
            probabilities = model.predict_proba(values)
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
            train_dates=("2026-07-20",),
            label_set="rank",
            label_version="v1",
            label_column="target",
        )
        predictions = inference_model.predict(
            np.array([[float("nan")]], dtype=float)
        )
    """

    model: PredictionModel
    preprocess: FittedPreprocessor
    feature_set: str
    feature_version: str
    train_dates: tuple[str, ...]
    label_set: str
    label_version: str
    label_column: str
    prediction_method: Literal["value", "positive_probability"] = "value"

    def __post_init__(self) -> None:
        if not isinstance(self.model, PredictionModel):
            raise TypeError("model must expose predict(values)")
        if not isinstance(self.preprocess, FittedPreprocessor):
            raise TypeError("preprocess must be a FittedPreprocessor")
        if self.prediction_method == "positive_probability":
            if not isinstance(self.model, ProbabilityModel) or not np.array_equal(self.model.classes_, [0, 1]):
                raise TypeError("positive_probability requires a binary model with classes [0, 1]")

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the actual ordered columns fitted during training.

        Example:
            names = inference_model.feature_names
        """
        return self.preprocess.feature_names

    def predict(self, raw_values: np.ndarray) -> np.ndarray:
        """Return float64 predictions in input order; dropped rows remain NaN.

        Example:
            predictions = inference_model.predict(
                np.array([[1.0], [float("nan")]], dtype=float)
            )
        """
        keep_rows, transformed = self.preprocess.transform(raw_values)
        aligned = np.full(keep_rows.shape[0], np.nan, dtype=np.float64)
        if transformed.shape[0] == 0:
            return aligned

        with threadpool_limits(limits=1):
            if self.prediction_method == "positive_probability":
                predictions = np.asarray(self.model.predict_proba(transformed)[:, 1], dtype=float)
            else:
                predictions = np.asarray(self.model.predict(transformed), dtype=float)
        if predictions.ndim != 1 or predictions.shape[0] != transformed.shape[0]:
            raise RuntimeError(
                "model predictions must be one-dimensional and match retained rows"
            )
        if not np.isfinite(predictions).all():
            raise RuntimeError("model predictions must be finite")
        aligned[keep_rows] = predictions
        return aligned
