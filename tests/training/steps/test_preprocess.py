# filepath: tests/training/steps/test_preprocess.py
"""Training-row alignment tests for the preprocessing Step."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.config.model_config import MissingConfig, PreprocessingConfig
from src.training.context import TrainingContext, TrainingWindow
from src.training.steps.preprocess import PreprocessStep


def test_drop_keeps_training_labels_aligned_with_retained_rows() -> None:
    step = PreprocessStep(PreprocessingConfig(missing=MissingConfig(method="drop")))

    train_X, train_y, fitted = step(
        train_X=pd.DataFrame(
            {"factor": [1.0, np.nan, 3.0]},
            index=["a", "b", "c"],
        ),
        train_y=pd.Series([10.0, 20.0, 30.0], index=["a", "b", "c"]),
    )

    assert train_X.index.tolist() == ["a", "c"]
    assert train_y.to_dict() == {"a": 10.0, "c": 30.0}
    assert fitted.feature_names == ("factor",)


def test_run_fits_statistics_from_training_rows_only() -> None:
    context = TrainingContext(
        window=TrainingWindow(train_dates=("2026-07-01",), model_ref=Path("/tmp/model.joblib")),
        train_X=pd.DataFrame({"factor": [1.0, 3.0]}),
        train_y=pd.Series([10.0, 30.0]),
    )

    PreprocessStep(PreprocessingConfig(missing=MissingConfig(method="mean"))).run(
        context
    )

    assert context.preprocess is not None
    assert context.preprocess.fill_values == (2.0,)
    assert context.train_y.tolist() == [10.0, 30.0]
