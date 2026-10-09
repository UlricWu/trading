# filepath: tests/training/models/test_tabular.py
"""Weighted estimators and public classification scores."""

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from src.config.model_config import PreprocessingConfig
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.inference_model import InferenceModel
from src.training.models.catalog import get_model_trainer
from src.training.models.tabular import train_tabular


def test_ridge_uses_sample_weights_in_its_fitted_intercept() -> None:
    fitted = train_tabular(X=pd.DataFrame({"x": [0.0, 0.0, 0.0]}), y=pd.Series([1.0, 0.0, 0.0]),
        model_params={"alpha": 100, "solver": "cholesky"}, estimator_type=Ridge,
        sample_weight=pd.Series([1.0, 0.5, 0.5]))
    np.testing.assert_array_equal(fitted.predict([[0.0]]), [0.5])


def test_saved_inference_contract_ranks_by_event_probability() -> None:
    X = pd.DataFrame({"x": [-2.0, -1.0, 1.0, 2.0]})
    raw = get_model_trainer("logistic_regression")(X=X, y=pd.Series([0, 0, 1, 1]), model_params={"C": 0.01, "solver": "lbfgs"})
    model = InferenceModel(model=raw, preprocess=FittedPreprocessor.fit(train_X=X, config=PreprocessingConfig()),
        feature_set="previous_day_daily_l2", feature_version="v1", train_dates=("2026-07-01",),
        label_set="next_day_high_top_decile", label_version="v1", label_column="y_event", prediction_method="positive_probability")
    scores = model.predict(X.to_numpy())
    assert np.all((scores > 0) & (scores < 1)) and np.all(np.diff(scores) > 0)
    np.testing.assert_array_equal(scores, raw.predict_proba(X.to_numpy())[:, 1])
