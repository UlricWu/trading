# filepath: src/data_system/steps/fact_ingest_step.py
"""Raw-source ingestion step in the offline data workflow."""

from __future__ import annotations

from src import logs
from src.access import meta
from src.config.app_config import AppConfig
from src.data_system.brokers.base import BrokerAdapter, DownloadPlan
from src.data_system.brokers.registry import BrokerRegistry
from src.data_system.context import DataContext
from src.observability.instrumentation import Instrumentation
from src.pipeline.step import PipelineStep


class FactIngestStep(PipelineStep[DataContext]):
    """
    Ingest configured sources as source-native raw objects.

    The step fetches one source object through a ``BrokerAdapter``, persists the
    vendor payload, and commits object-side ``meta.json`` only when a payload
    exists. Its workflow position is owned by
    ``docs/offline_workflow_contract.md``.
    """

    def __init__(
        self,
        *,
        app_cfg: AppConfig,
        inst: Instrumentation | None,
        broker_registry: BrokerRegistry,
    ) -> None:
        """Store pipeline configuration and instrumentation capability."""
        super().__init__(inst=inst)
        self._cfg = app_cfg
        self._broker_registry = broker_registry

    # ==================================================
    def run(self, ctx: DataContext) -> DataContext | None:
        """Run raw ingest for the configured `data.sources` selection."""
        logs.info(f"[FactIngest] start DATE={ctx.trade_date}")
        sources = self._cfg.data.sources
        if not sources:
            raise ValueError("FactIngestStep requires at least one source")

        broker_adapters: dict[str, BrokerAdapter] = {}
        available_payload = False
        missing_payloads = 0

        for source_name, source_cfg in sources.items():
            if not source_cfg.enabled:
                continue
            if source_cfg.raw_object is None:
                raise ValueError(
                    "FactIngestStep requires expanded ordinary sources with "
                    f"raw_object; source={source_name!r}"
                )

            plan = DownloadPlan(
                source_name=source_name,
                trade_date=ctx.trade_date,
                broker=source_cfg.broker,
                raw_object=source_cfg.raw_object,
            )
            meta_path = ctx.pm.raw_meta(
                broker=plan.broker,
                trade_date=plan.trade_date,
                source_name=plan.source_name,
            )
            if (
                meta.load(
                    meta_path=meta_path,
                    storage_root=ctx.pm.storage_root,
                )
                is not None
            ):
                available_payload = True
                logs.warning(
                    f"[FactIngest] meta hit → skip fact={plan.source_name} "
                    f"output={meta_path}"
                )
                continue

            adapter = broker_adapters.get(plan.broker)
            if adapter is None:
                adapter = self._broker_registry.create(
                    plan.broker,
                    app_cfg=self._cfg,
                )
                broker_adapters[plan.broker] = adapter
            logs.info(f"[FactIngest] downloading plan={plan}")
            fetched = adapter.fetch(record=plan, pm=ctx.pm)

            if fetched is None:
                logs.warning(
                    f"[FactIngest] no source data trade_date={ctx.trade_date} "
                    f"source={plan.source_name} broker={plan.broker}; "
                )
                missing_payloads += 1
                continue

            output_file = ctx.pm.raw_payload(
                broker=fetched.broker,
                trade_date=fetched.trade_date,
                source_name=fetched.source_name,
                payload_file=fetched.payload_file,
            )
            meta.write(
                payload_path=output_file,
                storage_root=ctx.pm.storage_root,
            )
            available_payload = True

        logs.info(f"[FactIngest] finished trade_date={ctx.trade_date}")
        if not available_payload:
            return None
        if missing_payloads:
            raise RuntimeError("source payloads are only partially available")
        return ctx
