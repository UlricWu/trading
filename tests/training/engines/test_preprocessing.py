# filepath: tests/training/engines/test_preprocessing.py
"""Train-owned preprocessing tests."""

from __future__ import annotations

import pandas as pd

from src.config.model_config import MissingConfig, PreprocessingConfig
from src.training.engines.preprocessing import (
    apply_preprocessing,
    fit_preprocessing,
)


def test_eval_preprocessing_uses_fill_values_fitted_on_training_rows() -> None:
    train_X = pd.DataFrame({"factor": [1.0, 3.0, None]})
    config = PreprocessingConfig(missing=MissingConfig(method="mean"))

    transformed_train, artifact = fit_preprocessing(
        train_X=train_X,
        config=config,
    )
    transformed_eval = apply_preprocessing(
        X=pd.DataFrame({"factor": [None]}),
        artifact=artifact,
    )

    assert transformed_train["factor"].tolist() == [1.0, 3.0, 2.0]
    assert dict(artifact.fill_values) == {"factor": 2.0}
    assert transformed_eval["factor"].tolist() == [2.0]
