# filepath: src/training/steps/dataset_build.py
"""Load only the explicitly selected training partitions."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from src import logs
from src.access.feature_label import read_features, read_labels
from src.config.model_config import FeatureLabelConfig
from src.training.context import TrainingContext
from src.training.engines.dataset import build_daily_training_dataset
from src.utils.path import PathManager


class DatasetBuildStep:
    """Build training inputs without reading evaluation data or labels.

    Example:
        step = DatasetBuildStep(pm=path_manager, dataset_cfg=model_config.dataset)
        context = step.run(TrainingContext(window=training_window))
    """

    def __init__(self, *, pm: PathManager, dataset_cfg: FeatureLabelConfig) -> None:
        """Bind committed input storage and the selected dataset.

        Example:
            step = DatasetBuildStep(pm=path_manager, dataset_cfg=model_config.dataset)
        """
        self._pm = pm
        self._dataset_cfg = dataset_cfg

    def load(self, *, train_dates: Sequence[str]) -> tuple[pd.DataFrame, pd.Series]:
        """Read exactly the given dates; filter unknown labels only for fitting.

        Example:
            X, y = step.load(train_dates=("2026-07-17", "2026-07-20"))
        """
        cfg = self._dataset_cfg
        X_parts: list[pd.DataFrame] = []
        y_parts: list[pd.Series] = []
        for trade_date in train_dates:
            features = read_features(
                pm=self._pm, feature_set=cfg.feature_set, version=cfg.feature_version,
                trade_date=trade_date, feature_names=cfg.feature_columns,
            )
            labels = read_labels(
                pm=self._pm, label_set=cfg.label_set, version=cfg.label_version,
                trade_date=trade_date, label_column=cfg.label_column,
            )
            keys = tuple(column for column in features if column not in cfg.feature_columns)
            if tuple(labels.columns[:-1]) != keys:
                raise RuntimeError("training dataset feature / label key schema mismatch")
            X, y = build_daily_training_dataset(
                feature_frame=features, label_frame=labels,
                feature_columns=cfg.feature_columns, label_column=cfg.label_column,
                key_columns=keys,
            )
            X_parts.append(X)
            y_parts.append(y)
        train_X = pd.concat(X_parts, ignore_index=True)
        train_y = pd.concat(y_parts, ignore_index=True)
        logs.info(f"✅ dataset build; train_dates={tuple(train_dates)} train_shape={train_X.shape}")
        return train_X, train_y

    def run(self, context: TrainingContext) -> TrainingContext:
        """Attach the window's training rows, leaving evaluation for later Steps.

        Example:
            context = step.run(TrainingContext(window=training_window))
        """
        context.train_X, context.train_y = self.load(train_dates=context.window.train_dates)
        return context
