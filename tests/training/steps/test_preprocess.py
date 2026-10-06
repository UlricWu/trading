# filepath: tests/training/steps/test_preprocess.py
"""Training-row alignment tests for the preprocessing Step."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

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


@pytest.mark.parametrize("method", ["constant", "mean", "median", "drop"])
def test_logs_distinguish_filled_cells_removed_rows_and_actual_fit_rows(
    method: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr("src.training.steps.preprocess.logs", logger)
    config = PreprocessingConfig(missing=MissingConfig(method=method, fill_value=0.0 if method == "constant" else None))
    X, y, _ = PreprocessStep(config)(
        train_X=pd.DataFrame({"a": [1.0, np.nan, 3.0], "b": [np.nan, 2.0, 4.0]}),
        train_y=pd.Series([0.1, 0.5, 0.9]),
    )
    text = logger.info.call_args.args[0]
    assert "含缺失特征：2 行" in text
    if method == "drop":
        assert "填充值：0 个单元格；移除：2 行；实际训练样本：1 行" in text
        assert y.tolist() == [0.9]
    else:
        assert "填充值：2 个单元格；移除：0 行；实际训练样本：3 行" in text
        assert y.tolist() == [0.1, 0.5, 0.9]
    assert np.isfinite(X.to_numpy()).all()
