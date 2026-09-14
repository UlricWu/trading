# filepath: src/data_system/steps/feature_build.py
"""Build feature partitions from processed inputs."""

from __future__ import annotations

from collections.abc import Mapping

import pyarrow as pa

from src import logs
from src.access import Access
from src.data_system.builders.feature_tushare_daily_basic import (
    TushareDailyBasicV1Builder,
)
from src.data_system.builders.registry import get_feature_builder
from src.data_system.context import DataContext
from src.data_system.steps._partition import _publish_partition
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
                who = (
                    f"feature; feature_set={feature_set} "
                    f"version={version} trade_date={trade_date}"
                )

                def _build_feature(
                    builder: TushareDailyBasicV1Builder = builder,
                    trade_date: str = trade_date,
                ) -> pa.Table:
                    input_dates = tuple(
                        self._access.recent_trade_dates(
                            end_date=trade_date,
                            sessions=builder.lookback_sessions + 1,
                        )
                    )
                    return builder.build(access=self._access, trade_dates=input_dates)

                rows = _publish_partition(
                    pm=self._pm,
                    paths=output_paths,
                    who=who,
                    build=_build_feature,
                )
                if rows is None:
                    logs.info(f"♻️ {who}")
                else:
                    logs.info(f"✅ {who} rows={rows}")
        return context
