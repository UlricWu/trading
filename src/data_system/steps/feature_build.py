# filepath: src/data_system/steps/feature_build.py
"""Build feature partitions from processed inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src import logs
from src.access import Access, meta
from src.config.data_config import FeatureSetConfig
from src.data_system.builders.base import FeatureBuilder
from src.data_system.builders.registry import get_feature_builder
from src.data_system.context import DataContext
from src.utils import table_ops
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class _FeatureOperation:
    feature_set: str
    version: str
    builder: FeatureBuilder


class FeatureBuildStep:
    """Materialize feature sets already selected by the workflow.

    Example:
        build_features = FeatureBuildStep(
            pm=path_manager,
            access=access,
            processed_version="v1",
            feature_sets=feature_sets,
        )
        build_features.run(
            DataContext(
                start="2026-07-01",
                end="2026-07-20",
                trade_dates=("2026-07-20",),
            )
        )
    """

    def __init__(
        self,
        *,
        pm: PathManager,
        access: Access,
        processed_version: str,
        feature_sets: Mapping[str, FeatureSetConfig],
    ) -> None:
        """Resolve and bind all selected feature builders.

        Example:
            build_features = FeatureBuildStep(
                pm=path_manager,
                access=access,
                processed_version="v1",
                feature_sets=feature_sets,
            )
        """
        self._pm = pm
        self._access = access
        self._processed_version = processed_version
        self._operations = tuple(
            _FeatureOperation(
                feature_set=feature_set,
                version=config.version,
                builder=get_feature_builder(feature_set, config.version),
            )
            for feature_set, config in feature_sets.items()
        )

    def __call__(self, trade_date: str) -> None:
        """Build enabled feature partitions for one trade date.

        Example:
            build_features("2026-07-20")
        """
        for operation in self._operations:
            logs.info(f"build feature_set={operation.feature_set}")
            output_meta = self._pm.feature_meta(
                feature_set=operation.feature_set,
                version=operation.version,
                trade_date=trade_date,
            )
            output_path = self._pm.feature_data(
                feature_set=operation.feature_set,
                version=operation.version,
                trade_date=trade_date,
            )
            if (
                meta.find(
                    pm=self._pm,
                    meta_path=output_meta,
                    expected_payload_path=output_path,
                )
                is not None
            ):
                logs.info(
                    f"meta hit -> skip "
                    f"feature_set={operation.feature_set} "
                    f"trade_date={trade_date}"
                )
                continue

            table = operation.builder.read_input(
                access=self._access,
                pm=self._pm,
                processed_version=self._processed_version,
                trade_date=trade_date,
            )
            features = operation.builder.build_partition(table)
            table_ops.require_nonempty(
                features,
                who=(
                    f"FeatureBuild feature_set={operation.feature_set} "
                    f"trade_date={trade_date}"
                ),
            )
            write_parquet_atomic(output_file=output_path, table=features)
            meta.commit(
                pm=self._pm,
                payload_path=output_path,
            )

    def run(self, context: DataContext) -> DataContext:
        """Build every selected feature set over all resolved trade dates.

        Example:
            next_context = build_features.run(
                DataContext(
                    start="2026-07-01",
                    end="2026-07-20",
                    trade_dates=("2026-07-20",),
                )
            )
        """
        for trade_date in context.trade_dates:
            self(trade_date)
        return context
