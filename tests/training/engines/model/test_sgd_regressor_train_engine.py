# filepath: tests/training/engines/model/test_sgd_regressor_train_engine.py
"""Fresh-model and input-boundary tests for SGD training."""

from __future__ import annotations

import pandas as pd
import pytest

from src.training.engines.model.sgd_regressor_train_engine import (
    SklearnSGDRegressorTrainEngine,
)


def test_each_train_call_returns_a_fresh_model() -> None:
    engine = SklearnSGDRegressorTrainEngine(model_params={"random_state": 7})
    features = pd.DataFrame({"factor": [0.0, 1.0]})
    labels = pd.Series([0.0, 1.0])

    first = engine.train(X=features, y=labels)
    second = engine.train(X=features, y=labels)

    assert first is not second
    assert first.n_iter_ == 1
    assert second.n_iter_ == 1


def test_train_rejects_misaligned_input_lengths() -> None:
    engine = SklearnSGDRegressorTrainEngine()

    with pytest.raises(ValueError, match="input length mismatch"):
        engine.train(
            X=pd.DataFrame({"factor": [0.0, 1.0]}),
            y=pd.Series([0.0]),
        )
