# filepath: tests/training/steps/test_dataset_build.py
"""Explicit-date loading tests for offline training datasets."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from src.access import meta
from src.config.model_config import FeatureLabelConfig
from src.training.steps.dataset_build import DatasetBuildStep
from src.utils.path import PathManager


def test_loader_consumes_the_supplied_window_without_calendar_resolution(
    tmp_path: Path,
) -> None:
    path_manager = PathManager(tmp_path)
    dataset_config = FeatureLabelConfig(
        feature_set="features",
        feature_version="v1",
        label_set="labels",
        label_version="v1",
        feature_columns=["factor"],
        label_column="target",
    )
    for index, trade_date in enumerate(
        ("2026-07-01", "2026-07-02", "2026-07-03"),
        start=1,
    ):
        feature_path = path_manager.feature_object(
            feature_set="features",
            version="v1",
            trade_date=trade_date,
        ).payload_path
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "symbol": ["600000"],
                "trade_date": [trade_date],
                "factor": [float(index)],
            }
        ).to_parquet(
            feature_path,
            index=False,
        )
        meta.commit(pm=path_manager, payload_path=feature_path)

        label_path = path_manager.label_object(
            label_set="labels",
            version="v1",
            trade_date=trade_date,
        ).payload_path
        label_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "symbol": ["600000"],
                "trade_date": [trade_date],
                "target": [index / 10],
            }
        ).to_parquet(
            label_path,
            index=False,
        )
        meta.commit(pm=path_manager, payload_path=label_path)

    # Evaluation data is intentionally unavailable; training must not read it.
    for paths in (
        path_manager.feature_object(feature_set="features", version="v1", trade_date="2026-07-03"),
        path_manager.label_object(label_set="labels", version="v1", trade_date="2026-07-03"),
    ):
        paths.payload_path.unlink()
        paths.meta_path.unlink()

    loader = DatasetBuildStep(
        pm=path_manager,
        dataset_cfg=dataset_config,
        clock=lambda: 0.0,
    )

    train_X, train_y = loader.load(
        train_dates=("2026-07-01", "2026-07-02"),
    )

    assert train_X["factor"].tolist() == [1.0, 2.0]
    assert train_y.tolist() == [0.1, 0.2]


def test_dataset_log_counts_missing_labels_separately_and_throttles_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Mock()
    monkeypatch.setattr("src.training.steps.dataset_build.logs", logger)
    features = pd.DataFrame({"symbol": ["a", "b"], "trade_date": ["2026-07-01"] * 2, "factor": [np.nan, 1.0]})
    labels = features[["symbol", "trade_date"]].assign(target=[0.5, np.nan])
    monkeypatch.setattr("src.training.steps.dataset_build.read_features", Mock(return_value=features))
    monkeypatch.setattr("src.training.steps.dataset_build.read_labels", Mock(return_value=labels))
    clock = Mock(side_effect=[0.0, 4.99, 5.0, 9.99, 10.0, 99.0])
    dates = ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07")
    loader = DatasetBuildStep(
        pm=PathManager(tmp_path), clock=clock,
        dataset_cfg=FeatureLabelConfig(feature_set="features", feature_version="v1", feature_columns=["factor"],
                                       label_set="labels", label_version="v1", label_column="target"),
    )
    X, y = loader.load(train_dates=dates)
    assert len(X) == 5 and X["factor"].isna().all() and y.tolist() == [0.5] * 5
    messages = [call.args[0] for call in logger.info.call_args_list]
    progress = [message for message in messages if message.startswith("⏳")]
    assert len(progress) == 2
    assert "已读取 2 / 5 个交易日" in progress[0] and "已读取 4 / 5 个交易日" in progress[1]
    assert "2026-07-01 ~ 2026-07-07（5 个交易日）" in messages[0]
    assert "原始样本：10 行；缺标签移除：5 行；标签筛选后：5 行" in messages[-1]
    assert clock.call_count == 6
