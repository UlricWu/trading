# filepath: tests/training/steps/test_dataset_build.py
"""Explicit-date loading tests for offline training datasets."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

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
        feature_path = path_manager.feature_data(
            feature_set="features",
            version="v1",
            trade_date=trade_date,
        )
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

        label_path = path_manager.label_data(
            label_set="labels",
            version="v1",
            trade_date=trade_date,
        )
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

    loader = DatasetBuildStep(
        pm=path_manager,
        dataset_cfg=dataset_config,
    )

    (train_X, train_y), (eval_X, eval_y) = loader.load(
        train_dates=("2026-07-01", "2026-07-02"),
        eval_date="2026-07-03",
    )

    assert train_X["factor"].tolist() == [1.0, 2.0]
    assert train_y.tolist() == [0.1, 0.2]
    assert eval_X["factor"].tolist() == [3.0]
    assert eval_y.tolist() == [0.3]
