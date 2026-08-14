# filepath: src/training/engines/model/sgd_regressor_train_engine.py
from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from sklearn.linear_model import SGDRegressor


class SklearnSGDRegressorTrainEngine:
    """Train one SGD regressor from validated in-memory inputs.

    Contract:
    - Engine NEVER infers y
    - Engine NEVER reads files
    - Engine ONLY trusts provided X / y

    Example:
        engine = SklearnSGDRegressorTrainEngine(model_params={"alpha": 0.001})
        model = engine.train(X=train_X, y=train_y)
    """

    def __init__(
        self,
        *,
        model_params: Mapping[str, object] | None = None,
    ) -> None:
        """Bind model parameters used for each fresh regressor.

        Example:
            engine = SklearnSGDRegressorTrainEngine(
                model_params={"alpha": 0.001}
            )
        """
        self.model_params = dict(model_params or {})

    def train(
        self,
        *,
        X: pd.DataFrame,
        y: pd.Series,
    ) -> SGDRegressor:
        """Fit and return one fresh SGD regressor.

        Example:
            model = engine.train(X=train_X, y=train_y)
        """
        if len(X) != len(y):
            raise ValueError(f"training input length mismatch: X={len(X)} y={len(y)}")
        X_values = np.asarray(X, dtype=float)
        y_values = np.asarray(y, dtype=float).reshape(-1)
        if not np.isfinite(X_values).all() or not np.isfinite(y_values).all():
            raise ValueError("training inputs must contain only finite values")

        model = SGDRegressor(**self.model_params)
        model.partial_fit(X_values, y_values)

        return model
