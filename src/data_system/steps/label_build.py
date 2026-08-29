# filepath: src/data_system/steps/label_build.py
"""Build selected label partitions from processed inputs."""

from __future__ import annotations

from collections.abc import Mapping

from src import logs
from src.access import Access
from src.data_system.builders.registry import get_label_builder
from src.data_system.context import DataContext
from src.data_system.steps._derived_partition import _publish_derived_partition
from src.utils.path import PathManager


class LabelBuildStep:
    """Materialize all selected label sets over resolved arrival dates.

    Example:
        step = LabelBuildStep(
            pm=path_manager,
            access=access,
            label_versions={"daily_close_return_rank_d1": "v1"},
        )
        step.run(
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
        label_versions: Mapping[str, str],
    ) -> None:
        """Resolve and bind every selected label builder.

        Example:
            step = LabelBuildStep(
                pm=path_manager,
                access=access,
                label_versions={"daily_close_return_rank_d1": "v1"},
            )
        """
        self._pm = pm
        self._access = access
        self._builders = {
            (label_set, version): get_label_builder(
                label_set,
                version,
            )
            for label_set, version in label_versions.items()
        }

    def run(self, context: DataContext) -> DataContext:
        """Build all mature labels over the Context arrival dates.

        Example:
            next_context = step.run(
                DataContext(
                    start="2026-07-01",
                    end="2026-07-20",
                    trade_dates=("2026-07-20",),
                )
            )
        """
        for arrival_date in context.trade_dates:
            for (label_set, version), builder in self._builders.items():
                input_dates = tuple(
                    self._access.recent_trade_dates(
                        end_date=arrival_date,
                        sessions=builder.lookahead + 1,
                    )
                )
                target_date = input_dates[0]
                output_meta = self._pm.label_meta(
                    label_set=label_set,
                    version=version,
                    trade_date=target_date,
                )
                output_path = self._pm.label_data(
                    label_set=label_set,
                    version=version,
                    trade_date=target_date,
                )
                rows = _publish_derived_partition(
                    pm=self._pm,
                    meta_path=output_meta,
                    output_path=output_path,
                    build=lambda builder=builder, input_dates=input_dates: (
                        builder.build(
                            access=self._access,
                            trade_dates=input_dates,
                        )
                    ),
                    who=(f"LabelBuild label_set={label_set} trade_date={target_date}"),
                )
                if rows is None:
                    logs.info(
                        f"♻️ label meta hit; label_set={label_set} version={version} "
                        f"trade_date={target_date} maturity_date={input_dates[-1]}"
                    )
                else:
                    logs.info(
                        f"✅ label publish; label_set={label_set} version={version} "
                        f"trade_date={target_date} maturity_date={input_dates[-1]} "
                        f"rows={rows}"
                    )
        return context
