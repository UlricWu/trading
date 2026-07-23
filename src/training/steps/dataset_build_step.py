# filepath: src/training/steps/dataset_build_step.py
from __future__ import annotations

import pandas as pd
import pyarrow.parquet as pq

from src import logs
from src.access import meta
from src.access.access import Slice
from src.config.model_config import FeatureLabelConfig
from src.pipeline.step import PipelineStep
from src.training.context import TrainingContext
from src.training.engines.dataset_build_engine import DatasetBuildEngine


class DatasetBuildStep(PipelineStep[TrainingContext]):
    """
    Load selected formal training inputs for the current train/eval dates.

    The selected `feature_set` / `label_set` identities come from
    `ModelConfig.dataset`. This step keeps IO and context mutation at the
    pipeline edge and delegates in-memory sample construction to
    DatasetBuildEngine.
    """

    def __init__(self, dataset_cfg: FeatureLabelConfig) -> None:
        super().__init__()
        self.dataset_cfg = dataset_cfg
        self.engine = DatasetBuildEngine()

    # ==============================================================
    # Entry
    # ==============================================================
    def run(self, ctx: TrainingContext) -> TrainingContext:
        ctx.train_X, ctx.train_y = self._build_train_window(ctx=ctx)
        if ctx.eval_date:
            ctx.eval_X, ctx.eval_y = self._build_one_day(ctx=ctx, day=ctx.eval_date)

        logs.info(
            f"[DatasetBuild] train_start_date={ctx.train_start_date} "
            f"train_end_date={ctx.train_end_date} "
            f"train_shape={ctx.train_X.shape} "
            f"eval_date={ctx.eval_date} "
            f"eval_shape={0 if ctx.eval_date == '' else ctx.eval_X.shape}"
        )
        return ctx

    def _build_train_window(
        self,
        *,
        ctx: TrainingContext,
    ) -> tuple[pd.DataFrame, pd.Series]:
        if not ctx.train_start_date or not ctx.train_end_date:
            raise RuntimeError(
                "[DatasetBuild] train_start_date / train_end_date not set"
            )

        train_days = Slice(
            pm=ctx.pm,
            trade_date=ctx.train_end_date,
            version="v1",
        ).trade_dates(start_date=ctx.train_start_date)
        if not train_days:
            raise RuntimeError(
                "[DatasetBuild] no tradable train dates "
                f"start={ctx.train_start_date} end={ctx.train_end_date}"
            )
        if (
            train_days[0] != ctx.train_start_date
            or train_days[-1] != ctx.train_end_date
        ):
            raise RuntimeError(
                "[DatasetBuild] train window boundaries are not tradable dates: "
                f"start={ctx.train_start_date} end={ctx.train_end_date} "
                f"tradable_start={train_days[0]} tradable_end={train_days[-1]}"
            )

        X_parts: list[pd.DataFrame] = []
        y_parts: list[pd.Series] = []
        for day in train_days:
            X, y = self._build_one_day(ctx=ctx, day=day)
            if X.empty:
                continue
            X_parts.append(X)
            y_parts.append(y)

        if not X_parts:
            return (
                pd.DataFrame(columns=self.dataset_cfg.feature_columns),
                pd.Series(dtype=float),
            )

        return (
            pd.concat(X_parts, axis=0, ignore_index=True),
            pd.concat(y_parts, axis=0, ignore_index=True),
        )

    def _build_one_day(
        self,
        *,
        ctx: TrainingContext,
        day: str,
    ) -> tuple[pd.DataFrame, pd.Series]:
        dataset_cfg = self.dataset_cfg
        feature_path = ctx.pm.feature_data(
            feature_set=dataset_cfg.feature_set,
            version=dataset_cfg.feature_version,
            trade_date=day,
        )
        loaded_feature = meta.load(
            meta_path=ctx.pm.feature_meta(
                feature_set=dataset_cfg.feature_set,
                version=dataset_cfg.feature_version,
                trade_date=day,
            ),
            storage_root=ctx.pm.storage_root,
            expected_payload_path=feature_path,
        )
        if loaded_feature is None:
            raise FileNotFoundError(
                "formal feature object is unavailable: "
                f"feature_set={dataset_cfg.feature_set}, "
                f"version={dataset_cfg.feature_version}, trade_date={day}"
            )
        feat_df = pq.ParquetFile(loaded_feature.payload_path).read().to_pandas()
        label_path = ctx.pm.label_data(
            label_set=dataset_cfg.label_set,
            version=dataset_cfg.label_version,
            trade_date=day,
        )
        loaded_label = meta.load(
            meta_path=ctx.pm.label_meta(
                label_set=dataset_cfg.label_set,
                version=dataset_cfg.label_version,
                trade_date=day,
            ),
            storage_root=ctx.pm.storage_root,
            expected_payload_path=label_path,
        )
        if loaded_label is None:
            raise FileNotFoundError(
                "formal label object is unavailable: "
                f"label_set={dataset_cfg.label_set}, "
                f"version={dataset_cfg.label_version}, trade_date={day}"
            )
        lab_df = pq.ParquetFile(loaded_label.payload_path).read().to_pandas()

        adj_df = None
        if dataset_cfg.adjustment.method != "raw":
            adj_df = Slice(
                pm=ctx.pm,
                trade_date=day,
                version=dataset_cfg.adjustment.version,
            ).daily(dataset_cfg.adjustment.dataset_name)

        X, y = self.engine.build_one_day(
            feature_frame=feat_df,
            label_frame=lab_df,
            feature_columns=dataset_cfg.feature_columns,
            label_column=dataset_cfg.label_column,
            drop_na=dataset_cfg.drop_na,
            adjustment=dataset_cfg.adjustment.method,
            adjustment_refdata_frame=adj_df,
            asof_date=day,
        )

        return X, y
