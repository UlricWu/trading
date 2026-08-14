# filepath: src/data_system/steps/label_build_step.py
"""Build selected label partitions from processed inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src import logs
from src.access import Access, meta
from src.config.data_config import LabelSetConfig
from src.data_system.builders.base import LabelBuilder
from src.data_system.builders.registry import get_label_builder
from src.data_system.context import DataContext
from src.utils import table_ops
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class _LabelOperation:
    label_set: str
    version: str
    builder: LabelBuilder
    lookahead: int


class LabelBuildStep:
    """Materialize all selected label sets over resolved arrival dates.

    Example:
        step = LabelBuildStep(
            pm=path_manager,
            access=access,
            processed_version="v1",
            label_sets=label_sets,
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
        processed_version: str,
        label_sets: Mapping[str, LabelSetConfig],
    ) -> None:
        """Resolve and bind every selected label builder.

        Example:
            step = LabelBuildStep(
                pm=path_manager,
                access=access,
                processed_version="v1",
                label_sets=label_sets,
            )
        """
        self._pm = pm
        self._access = access
        self._processed_version = processed_version
        operations: list[_LabelOperation] = []
        for label_set, config in label_sets.items():
            builder = get_label_builder(label_set, config.version)
            operations.append(
                _LabelOperation(
                    label_set=label_set,
                    version=config.version,
                    builder=builder,
                    lookahead=max(
                        builder.target_lookahead(column)
                        for column in builder.output_columns
                    ),
                )
            )
        self._operations = tuple(operations)

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
            for operation in self._operations:
                input_dates = tuple(
                    self._access.recent_trade_dates(
                        end_date=arrival_date,
                        sessions=operation.lookahead + 1,
                    )
                )
                target_date = input_dates[0]
                logs.info(
                    f"[LabelBuild] build label_set={operation.label_set} "
                    f"trade_date={target_date} maturity_date={input_dates[-1]}"
                )
                output_meta = self._pm.label_meta(
                    label_set=operation.label_set,
                    version=operation.version,
                    trade_date=target_date,
                )
                output_path = self._pm.label_data(
                    label_set=operation.label_set,
                    version=operation.version,
                    trade_date=target_date,
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
                        "[LabelBuild] meta hit -> skip "
                        f"label_set={operation.label_set} trade_date={target_date}"
                    )
                    continue

                table = operation.builder.read_input(
                    access=self._access,
                    pm=self._pm,
                    processed_version=self._processed_version,
                    trade_dates=input_dates,
                )
                labels = operation.builder.build_partition(table)
                table_ops.require_nonempty(
                    labels,
                    who=(
                        f"LabelBuild label_set={operation.label_set} "
                        f"trade_date={target_date}"
                    ),
                )
                write_parquet_atomic(output_file=output_path, table=labels)
                meta.commit(
                    pm=self._pm,
                    payload_path=output_path,
                )
        return context
