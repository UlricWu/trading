# filepath: src/data_system/steps/label_build_step.py
"""Build label partitions from processed inputs."""

from __future__ import annotations

from src import logs
from src.access import meta
from src.config.app_config import AppConfig
from src.data_system.builders.registry import get_label_builder
from src.data_system.context import DataContext
from src.observability.instrumentation import Instrumentation
from src.pipeline.step import PipelineStep
from src.utils.parquet_writer import write_parquet_atomic


class LabelBuildStep(PipelineStep[DataContext]):
    """Materialize enabled label sets selected by the workflow."""

    def __init__(
        self,
        *,
        app_cfg: AppConfig,
        inst: Instrumentation | None,
        allowed_sets: frozenset[str] | None = None,
    ) -> None:
        super().__init__(inst=inst)
        self._cfg = app_cfg
        self.allowed_sets = allowed_sets

    def run(self, ctx: DataContext) -> DataContext:
        """Build enabled label partitions for one trade date."""
        for label_set, label_cfg in self._cfg.data.label_sets.items():
            if not label_cfg.enabled:
                continue
            if self.allowed_sets is not None and label_set not in self.allowed_sets:
                continue

            builder = get_label_builder(label_set, label_cfg.version)
            logs.info(f"[LabelBuild] build label_set={label_set}")
            output_meta = ctx.pm.label_meta(
                label_set=label_set,
                version=label_cfg.version,
                trade_date=ctx.trade_date,
            )
            output_path = ctx.pm.label_data(
                label_set=label_set,
                version=label_cfg.version,
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
                    f"[LabelBuild] meta hit -> skip label_set={label_set} "
                    f"trade_date={ctx.trade_date}"
                )
                continue

            table = builder.read_input(
                pm=ctx.pm,
                trade_date=ctx.trade_date,
            )
            labels = builder.build_partition(table)
            if labels.num_rows == 0:
                raise RuntimeError(
                    f"[LabelBuild] empty label output label_set={label_set} "
                    f"trade_date={ctx.trade_date}"
                )
            write_parquet_atomic(output_file=output_path, table=labels)
            meta.commit(
                pm=ctx.pm,
                payload_path=output_path,
            )

        return ctx
