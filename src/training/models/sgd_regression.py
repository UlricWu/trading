# filepath: src/training/models/sgd_regression.py
"""Fit the concrete SGD regression model used by offline training."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from sklearn.linear_model import SGDRegressor


def train_sgd_regression(
    *,
    X: pd.DataFrame,
    y: pd.Series,
    model_params: Mapping[str, object],
) -> SGDRegressor:
    """Fit and return one fresh SGD regressor.

    Example:
        model = train_sgd_regression(
            X=train_X,
            y=train_y,
            model_params={"alpha": 0.001},
        )
    """
    if len(X) != len(y):
        raise ValueError(f"training input length mismatch: X={len(X)} y={len(y)}")
    X_values = np.asarray(X, dtype=float)
    y_values = np.asarray(y, dtype=float).reshape(-1)
    if not np.isfinite(X_values).all() or not np.isfinite(y_values).all():
        raise ValueError("training inputs must contain only finite values")

    model = SGDRegressor(**dict(model_params))
    model.partial_fit(X_values, y_values)
    return model
