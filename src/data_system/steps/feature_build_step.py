# filepath: src/data_system/steps/feature_build_step.py
"""Build feature partitions from processed inputs."""

from __future__ import annotations

from src import logs
from src.access import meta
from src.config.app_config import AppConfig
from src.utils import table_ops
from src.data_system.builders.registry import get_feature_builder
from src.data_system.context import DataContext
from src.utils.parquet_writer import write_parquet_atomic


class FeatureBuildStep:
    """Materialize feature sets already selected by the workflow.

    Example:
        step = FeatureBuildStep(app_cfg=selected_config)
        step(context)
    """

    def __init__(
        self,
        *,
        app_cfg: AppConfig,
    ) -> None:
        """Store the selected feature configuration.

        Example:
            step = FeatureBuildStep(app_cfg=selected_config)
        """
        self._cfg = app_cfg

    def __call__(self, ctx: DataContext) -> None:
        """Build enabled feature partitions for one trade date.

        Example:
            step(context)
        """
        for feature_set, feature_cfg in self._cfg.data.feature_sets.items():
            builder = get_feature_builder(feature_set, feature_cfg.version)
            logs.info(f"[FeatureBuild] build feature_set={feature_set}")
            output_meta = ctx.pm.feature_meta(
                feature_set=feature_set,
                version=feature_cfg.version,
                trade_date=ctx.trade_date,
            )
            output_path = ctx.pm.feature_data(
                feature_set=feature_set,
                version=feature_cfg.version,
                trade_date=ctx.trade_date,
            )
            if (
                meta.find(
                    pm=ctx.pm,
                    meta_path=output_meta,
                    expected_payload_path=output_path,
                )
                is not None
            ):
                logs.info(
                    f"[FeatureBuild] meta hit -> skip feature_set={feature_set} "
                    f"trade_date={ctx.trade_date}"
                )
                continue

            table = builder.read_input(
                pm=ctx.pm,
                trade_date=ctx.trade_date,
            )
            features = builder.build_partition(table)
            table_ops.require_nonempty(
                features,
                who=(
                    f"FeatureBuild feature_set={feature_set} "
                    f"trade_date={ctx.trade_date}"
                ),
            )
            write_parquet_atomic(output_file=output_path, table=features)
            meta.commit(
                pm=ctx.pm,
                payload_path=output_path,
            )
