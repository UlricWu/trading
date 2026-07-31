# filepath: src/data_system/steps/label_build_step.py
"""Build label partitions from processed inputs."""

from __future__ import annotations

from src import logs
from src.access import meta
from src.utils import table_ops
from src.data_system.builders.base import LabelBuilder
from src.data_system.context import DataContext
from src.utils.parquet_writer import write_parquet_atomic


class LabelBuildStep:
    """Materialize one label partition from a complete calendar window.

    Example:
        step = LabelBuildStep(
            label_set="daily_t1_net_excess_rank",
            version="v1",
            builder=label_builder,
            input_dates=(
                "2026-07-16",
                "2026-07-17",
                "2026-07-20",
            ),
        )
        step(context)
    """

    def __init__(
        self,
        *,
        label_set: str,
        version: str,
        builder: LabelBuilder,
        input_dates: tuple[str, ...],
    ) -> None:
        """Store one selected builder and its complete input dates.

        Example:
            step = LabelBuildStep(
                label_set="daily_t1_net_excess_rank",
                version="v1",
                builder=label_builder,
                input_dates=(
                    "2026-07-16",
                    "2026-07-17",
                    "2026-07-20",
                ),
            )
        """
        self._label_set = label_set
        self._version = version
        self._builder = builder
        self._input_dates = input_dates

    def __call__(self, ctx: DataContext) -> None:
        """Build the selected label partition for the context date.

        Example:
            step(context)
        """
        logs.info(
            f"[LabelBuild] build label_set={self._label_set} "
            f"trade_date={ctx.trade_date} maturity_date={self._input_dates[-1]}"
        )
        output_meta = ctx.pm.label_meta(
            label_set=self._label_set,
            version=self._version,
            trade_date=ctx.trade_date,
        )
        output_path = ctx.pm.label_data(
            label_set=self._label_set,
            version=self._version,
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
                f"[LabelBuild] meta hit -> skip label_set={self._label_set} "
                f"trade_date={ctx.trade_date}"
            )
            return

        table = self._builder.read_input(
            pm=ctx.pm,
            trade_dates=self._input_dates,
        )
        labels = self._builder.build_partition(table)
        table_ops.require_nonempty(
            labels,
            who=(f"LabelBuild label_set={self._label_set} trade_date={ctx.trade_date}"),
        )
        write_parquet_atomic(output_file=output_path, table=labels)
        meta.commit(
            pm=ctx.pm,
            payload_path=output_path,
        )
