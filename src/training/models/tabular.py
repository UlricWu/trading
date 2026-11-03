# filepath: src/training/models/tabular.py
"""Fit fixed tabular estimators with explicit sample weights."""

from collections.abc import Mapping
import warnings

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression, Ridge
from threadpoolctl import threadpool_limits

from src import logs

TabularEstimator = Ridge | LogisticRegression | HistGradientBoostingRegressor | HistGradientBoostingClassifier


def train_tabular(
    *, X: pd.DataFrame, y: pd.Series, model_params: Mapping[str, object],
    estimator_type: type[TabularEstimator], sample_weight: pd.Series | None = None,
) -> TabularEstimator:
    """Fit one new estimator; convergence failures terminate the training job.

    Example:
        model = train_tabular(X=X, y=y, model_params={"alpha": 100}, estimator_type=Ridge)
    """
    estimator = estimator_type(**dict(model_params))
    logs.info(f"▶️ 模型拟合; model={estimator_type.__name__} rows={len(X)} columns={X.shape[1]}")
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        estimator.fit(X.to_numpy(dtype=float), y.to_numpy(dtype=float), sample_weight=sample_weight)
    logs.info(f"✅ 模型拟合; model={estimator_type.__name__} rows={len(X)}")
    return estimator
