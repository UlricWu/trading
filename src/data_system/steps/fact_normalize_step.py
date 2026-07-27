# filepath: src/data_system/steps/fact_normalize_step.py
"""Raw-to-processed normalization step in the offline data workflow."""

from __future__ import annotations

from src import logs
from src.access import meta
from src.config.app_config import AppConfig
from src.data_system.context import DataContext
from src.data_system.normalize.profiles import NORMALIZE_PROFILES
from src.observability.instrumentation import Instrumentation
from src.pipeline.step import PipelineStep
from src.utils.parquet_writer import write_parquet_atomic


LEVEL2_TRADE_OUTPUTS = frozenset({"sh_trade", "sz_trade"})


class FactNormalizeStep(PipelineStep[DataContext]):
    """
    Normalize persisted raw sources into processed datasets.

    The step reads a raw object and its ``meta.json``, runs the broker-selected
    normalize profile, writes processed parquet outputs, and records upstream
    lineage. Its workflow position is owned by
    ``docs/offline_workflow_contract.md``.
    """

    def __init__(
        self,
        *,
        app_cfg: AppConfig,
        inst: Instrumentation | None,
    ) -> None:
        """Store selected data config and instrumentation capability."""
        super().__init__(inst=inst)
        self._cfg = app_cfg

    def run(self, ctx: DataContext) -> DataContext:
        """Normalize all selected sources for one `DataContext.trade_date`."""
        logs.info(f"[FactNormalize] start DATE={ctx.trade_date}")
        plans = [
            [source_name, source_cfg, output]
            for source_name, source_cfg in self._cfg.data.sources.items()
            for output in source_cfg.outputs
            if source_cfg.enabled and source_cfg.outputs
        ]

        for source_name, source_cfg, output in plans:
            normalize_profile = self._cfg.data.brokers[
                source_cfg.broker
            ].normalize_profile  # v1

            if normalize_profile is None:
                raise ValueError(
                    f"broker '{source_cfg.broker}' has no normalize_profile "
                    f"for source '{source_name}'"
                )

            processed_meta = ctx.pm.processed_meta(
                dataset_name=output,
                version=normalize_profile,
                trade_date=ctx.trade_date,
            )
            output_file = ctx.pm.processed_data(
                dataset_name=output,
                version=normalize_profile,
                trade_date=ctx.trade_date,
            )
            loaded_output = meta.find(
                pm=ctx.pm,
                meta_path=processed_meta,
                expected_payload_path=output_file,
            )
            if (
                output in LEVEL2_TRADE_OUTPUTS
                and loaded_output is not None
                and loaded_output.symbol_slices is None
            ):
                raise RuntimeError(
                    "Level-2 Meta has no symbol_slices: "
                    f"dataset={output}, meta_path={processed_meta}"
                )

            if loaded_output is not None:
                logs.info(
                    f"[FactNormalize] meta hit -> skip "
                    f"target={output} source={source_name}"
                )
                continue

            profile = NORMALIZE_PROFILES.get((source_cfg.broker, normalize_profile))
            if profile is None:
                raise KeyError(
                    f"normalize profile '{normalize_profile}' is not registered "
                    f"for broker '{source_cfg.broker}'"
                )
            logs.info(f"[FactNormalize] normalizing profile={profile}")

            raw_meta_path = ctx.pm.raw_meta(
                broker=source_cfg.broker,
                source_name=source_name,
                trade_date=ctx.trade_date,
            )
            loaded_input = meta.require(
                pm=ctx.pm,
                meta_path=raw_meta_path,
            )

            staging_candidate = ctx.pm.staging_payload(
                broker=source_cfg.broker,
                source_name=source_name,
                trade_date=ctx.trade_date,
                payload_file=loaded_input.payload_path.name,
            )
            input_file = loaded_input.payload_path
            if (
                staging_candidate.is_file()
                and staging_candidate.stat().st_size == loaded_input.size_bytes
            ):
                input_file = staging_candidate

            target = profile(
                input_file=input_file,
                raw_object=source_cfg.raw_object,
                target_name=output,
                output_name=output_file,
                trade_date=ctx.trade_date,
            )

            if target.table.num_rows == 0:
                logs.warning(
                    "normalize produced no processed outputs: "
                    f"source={source_name} output={output_file} "
                )
                continue

            write_parquet_atomic(
                output_file=output_file,
                table=target.table,
            )

            meta.commit(
                pm=ctx.pm,
                payload_path=output_file,
                upstream_meta_path=raw_meta_path,
                symbol_slices=target.symbol_slices,
            )

        logs.info(f"[FactNormalize] finished trade_date={ctx.trade_date}")
        return ctx
