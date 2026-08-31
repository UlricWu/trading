# filepath: src/data_system/steps/feature_build.py
"""Build feature partitions from processed inputs."""

from __future__ import annotations

from collections.abc import Mapping

from src import logs
from src.access import Access
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
            feature_versions={"tushare_daily_basic": "v1"},
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
        feature_versions: Mapping[str, str],
    ) -> None:
        """Resolve and bind all selected feature builders.

        Example:
            build_features = FeatureBuildStep(
                pm=path_manager,
                access=access,
                feature_versions={"tushare_daily_basic": "v1"},
            )
        """
        self._pm = pm
        self._access = access
        self._builders = {
            (feature_set, version): get_feature_builder(
                feature_set,
                version,
            )
            for feature_set, version in feature_versions.items()
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
                output_paths = self._pm.feature_object(
                    feature_set=feature_set,
                    version=version,
                    trade_date=trade_date,
                )
                rows = _publish_derived_partition(
                    pm=self._pm,
                    meta_path=output_paths.meta_path,
                    output_path=output_paths.payload_path,
                    build=lambda builder=builder, trade_date=trade_date: builder.build(
                        access=self._access,
                        trade_dates=tuple(
                            self._access.recent_trade_dates(
                                end_date=trade_date,
                                sessions=builder.lookback_sessions + 1,
                            )
                        ),
                    ),
                    who=(
                        f"FeatureBuild feature_set={feature_set} "
                        f"trade_date={trade_date}"
                    ),
                )
                if rows is None:
                    logs.info(
                        f"♻️ feature meta hit; feature_set={feature_set} "
                        f"version={version} trade_date={trade_date}"
                    )
                else:
                    logs.info(
                        f"✅ feature publish; feature_set={feature_set} "
                        f"version={version} trade_date={trade_date} rows={rows}"
                    )
        return context
