# filepath: src/training/steps/dataset_build_step.py
"""Load formal feature and label partitions for one training window."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pyarrow.parquet as pq

from src import logs
from src.access import meta
from src.config.model_config import FeatureLabelConfig
from src.training.context import TrainingContext
from src.training.engines.dataset_build_engine import DatasetBuildEngine
from src.utils.path import PathManager


class DatasetBuildStep:
    """Load selected formal inputs without querying the calendar again.

    Example:
        loader = DatasetBuildStep(
            pm=path_manager,
            dataset_cfg=model_config.dataset,
            processed_version="v1",
        )
        train, evaluation = loader.load(
            train_dates=("2026-07-17", "2026-07-20"),
            eval_date="2026-07-21",
        )
    """

    def __init__(
        self,
        *,
        pm: PathManager,
        dataset_cfg: FeatureLabelConfig,
        processed_version: str,
    ) -> None:
        """Bind formal storage and dataset identities.

        Example:
            loader = DatasetBuildStep(
                pm=path_manager,
                dataset_cfg=model_config.dataset,
                processed_version="v1",
            )
        """
        self._pm = pm
        self._dataset_cfg = dataset_cfg
        self._processed_version = processed_version
        self._engine = DatasetBuildEngine()

    def load(
        self,
        *,
        train_dates: Sequence[str],
        eval_date: str,
    ) -> tuple[
        tuple[pd.DataFrame, pd.Series],
        tuple[pd.DataFrame, pd.Series],
    ]:
        """Return training and evaluation feature/label pairs.

        Example:
            train, evaluation = loader.load(
                train_dates=("2026-07-17", "2026-07-20"),
                eval_date="2026-07-21",
            )
        """
        train_X_parts: list[pd.DataFrame] = []
        train_y_parts: list[pd.Series] = []
        for trade_date in train_dates:
            daily_X, daily_y = self._load_one_day(trade_date)
            if daily_X.empty:
                continue
            train_X_parts.append(daily_X)
            train_y_parts.append(daily_y)

        if train_X_parts:
            train_X = pd.concat(train_X_parts, axis=0, ignore_index=True)
            train_y = pd.concat(train_y_parts, axis=0, ignore_index=True)
        else:
            train_X = pd.DataFrame(columns=self._dataset_cfg.feature_columns)
            train_y = pd.Series(dtype=float)
        eval_X, eval_y = self._load_one_day(eval_date)
        logs.info(
            f"[DatasetBuild] train_start_date={train_dates[0]} "
            f"train_end_date={train_dates[-1]} train_shape={train_X.shape} "
            f"eval_date={eval_date} eval_shape={eval_X.shape}"
        )
        return (train_X, train_y), (eval_X, eval_y)

    def run(self, context: TrainingContext) -> TrainingContext:
        """Load the Context window and attach its train/evaluation partitions.

        Example:
            next_context = loader.run(
                TrainingContext(window=training_window)
            )
        """
        (
            (context.train_X, context.train_y),
            (
                context.eval_X,
                context.eval_y,
            ),
        ) = self.load(
            train_dates=context.window.train_dates,
            eval_date=context.window.eval_date,
        )
        return context

    def _load_one_day(self, trade_date: str) -> tuple[pd.DataFrame, pd.Series]:
        dataset_cfg = self._dataset_cfg
        feature_path = self._pm.feature_data(
            feature_set=dataset_cfg.feature_set,
            version=dataset_cfg.feature_version,
            trade_date=trade_date,
        )
        loaded_feature = meta.require(
            pm=self._pm,
            meta_path=self._pm.feature_meta(
                feature_set=dataset_cfg.feature_set,
                version=dataset_cfg.feature_version,
                trade_date=trade_date,
            ),
            expected_payload_path=feature_path,
        )
        feature_frame = pq.ParquetFile(loaded_feature.payload_path).read().to_pandas()

        label_path = self._pm.label_data(
            label_set=dataset_cfg.label_set,
            version=dataset_cfg.label_version,
            trade_date=trade_date,
        )
        loaded_label = meta.require(
            pm=self._pm,
            meta_path=self._pm.label_meta(
                label_set=dataset_cfg.label_set,
                version=dataset_cfg.label_version,
                trade_date=trade_date,
            ),
            expected_payload_path=label_path,
        )
        label_frame = pq.ParquetFile(loaded_label.payload_path).read().to_pandas()

        adjustment_frame = None
        if dataset_cfg.adjustment.method != "raw":
            adjustment_path = self._pm.processed_data(
                dataset_name=dataset_cfg.adjustment.dataset_name,
                version=self._processed_version,
                trade_date=trade_date,
            )
            loaded_adjustment = meta.require(
                pm=self._pm,
                meta_path=self._pm.processed_meta(
                    dataset_name=dataset_cfg.adjustment.dataset_name,
                    version=self._processed_version,
                    trade_date=trade_date,
                ),
                expected_payload_path=adjustment_path,
            )
            adjustment_frame = (
                pq.ParquetFile(loaded_adjustment.payload_path).read().to_pandas()
            )

        return self._engine.build_one_day(
            feature_frame=feature_frame,
            label_frame=label_frame,
            feature_columns=dataset_cfg.feature_columns,
            label_column=dataset_cfg.label_column,
            drop_na=dataset_cfg.drop_na,
            adjustment=dataset_cfg.adjustment.method,
            adjustment_refdata_frame=adjustment_frame,
            asof_date=trade_date,
        )
