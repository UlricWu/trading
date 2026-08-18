# filepath: src/data_system/steps/feature_build.py
"""Build feature partitions from processed inputs."""

from __future__ import annotations

from collections.abc import Mapping

from src import logs
from src.access import Access
from src.config.data_config import FeatureSetConfig
from src.data_system.builders.registry import get_feature_builder
from src.data_system.context import DataContext
from src.data_system.steps._derived_partition import _publish_derived_partition
from src.utils.path import PathManager


class FeatureBuildStep:
    """Materialize feature sets already selected by the workflow.

    Example:
        build_features = FeatureBuildStep(
            pm=path_manager,
            access=access,
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
        feature_sets: Mapping[str, FeatureSetConfig],
    ) -> None:
        """Resolve and bind all selected feature builders.

        Example:
            build_features = FeatureBuildStep(
                pm=path_manager,
                access=access,
                feature_sets=feature_sets,
            )
        """
        self._pm = pm
        self._access = access
        self._builders = {
            (feature_set, config.version): get_feature_builder(
                feature_set,
                config.version,
            )
            for feature_set, config in feature_sets.items()
        }

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
            for (feature_set, version), builder in self._builders.items():
                output_meta = self._pm.feature_meta(
                    feature_set=feature_set,
                    version=version,
                    trade_date=trade_date,
                )
                output_path = self._pm.feature_data(
                    feature_set=feature_set,
                    version=version,
                    trade_date=trade_date,
                )
                rows = _publish_derived_partition(
                    pm=self._pm,
                    meta_path=output_meta,
                    output_path=output_path,
                    build=lambda builder=builder, trade_date=trade_date: builder.build(
                        access=self._access,
                        trade_date=trade_date,
                    ),
                    who=(
                        f"FeatureBuild feature_set={feature_set} "
                        f"trade_date={trade_date}"
                    ),
                )
                if rows is None:
                    logs.info(
                        f"feature reused; feature_set={feature_set} "
                        f"version={version} trade_date={trade_date}"
                    )
                else:
                    logs.info(
                        f"feature published; feature_set={feature_set} "
                        f"version={version} trade_date={trade_date} rows={rows}"
                    )
        return context
