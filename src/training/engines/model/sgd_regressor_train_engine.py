# filepath: src/training/engines/model/sgd_regressor_train_engine.py
from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd
from sklearn.linear_model import SGDRegressor

from src.training.context import ModelState


class SklearnSGDRegressorTrainEngine:
    """Train one SGD regressor from validated in-memory inputs.

    Contract:
    - Engine NEVER infers y
    - Engine NEVER reads files
    - Engine ONLY trusts provided X / y
    """

    def __init__(
        self,
        *,
        model_params: Mapping[str, object] | None = None,
    ) -> None:
        self.model_params = dict(model_params or {})

    def train(
            self,
            *,
            X: pd.DataFrame,
            y: pd.Series,
            asof_day: str,
    ) -> ModelState:
        if len(X) != len(y):
            raise ValueError(
                f"training input length mismatch: X={len(X)} y={len(y)}"
            )
        X_values = np.asarray(X, dtype=float)
        y_values = np.asarray(y, dtype=float).reshape(-1)
        if not np.isfinite(X_values).all() or not np.isfinite(y_values).all():
            raise ValueError("training inputs must contain only finite values")

        model = SGDRegressor(**self.model_params)
        model.partial_fit(X_values, y_values)

        return ModelState(
            model=model,
            asof_day=asof_day,
            update_count=1,
            warm_start=False,
        )
