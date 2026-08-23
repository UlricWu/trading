# filepath: src/data_system/steps/fact_materialize.py
"""Materialize selected fact sources over formal trade dates."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from src import logs
from src.access import meta
from src.config.app_config import AppConfig
from src.config.data_config import SourceConfig
from src.data_system.brokers.base import BrokerAdapter, DownloadPlan
from src.data_system.context import DataContext
from src.data_system.normalize import NormalizeOperation
from src.utils import table_ops
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager

_EMPTY_EVENT_OUTPUTS = frozenset({"stock_st", "suspend_d"})
_LEVEL2_TRADE_OUTPUTS = frozenset({"sh_trade", "sz_trade"})


@dataclass(frozen=True, slots=True)
class _NormalizePlan:
    source_name: str
    source: SourceConfig
    output: str
    operation: NormalizeOperation


class FactMaterializeStep:
    """Ingest and normalize all selected facts over formal trade dates.

    Example:
        step = FactMaterializeStep(
            app_config=app_config,
            path_manager=path_manager,
            sources=fact_sources,
            broker_classes=broker_classes,
            normalize_operations=normalize_operations,
            processed_version="v1",
            adapter_cache={},
        )
        context = step.run(
            DataContext(
                start="2026-07-20",
                end="2026-07-20",
                trade_dates=("2026-07-20",),
            )
        )
    """

    def __init__(
        self,
        *,
        app_config: AppConfig,
        path_manager: PathManager,
        sources: Mapping[str, SourceConfig],
        broker_classes: Mapping[str, type[BrokerAdapter]],
        normalize_operations: Mapping[str, NormalizeOperation],
        processed_version: str,
        adapter_cache: MutableMapping[str, BrokerAdapter],
    ) -> None:
        """Resolve selected source capabilities before any date I/O.

        Example:
            step = FactMaterializeStep(
                app_config=app_config,
                path_manager=path_manager,
                sources=fact_sources,
                broker_classes=broker_classes,
                normalize_operations=normalize_operations,
                processed_version="v1",
                adapter_cache={},
            )
        """
        if not sources:
            raise ValueError("FactMaterializeStep requires at least one source")

        self._app_config = app_config
        self._path_manager = path_manager
        self._sources = dict(sources)
        self._broker_classes = dict(broker_classes)
        self._processed_version = processed_version
        self._adapter_cache = adapter_cache

        plans: list[_NormalizePlan] = []
        for source_name, source in self._sources.items():
            if source.broker not in self._broker_classes:
                raise KeyError(f"Broker '{source.broker}' is not registered")
            if not source.outputs:
                continue
            try:
                operation = normalize_operations[source.broker]
            except KeyError as exc:
                raise KeyError(
                    f"normalize operation is not bound for broker '{source.broker}'"
                ) from exc
            plans.extend(
                _NormalizePlan(
                    source_name=source_name,
                    source=source,
                    output=output,
                    operation=operation,
                )
                for output in source.outputs
            )
        self._normalize_plans = tuple(plans)

    def run(self, context: DataContext) -> DataContext:
        """Materialize all trade dates and report every wholly missing date.

        Example:
            context = step.run(
                DataContext(
                    start="2026-07-20",
                    end="2026-07-20",
                    trade_dates=("2026-07-20",),
                )
            )
        """
        missing_dates: list[str] = []
        for trade_date in context.trade_dates:
            if not self._ingest_sources(trade_date):
                missing_dates.append(trade_date)
                continue
            self._normalize_sources(trade_date)

        if missing_dates:
            raise RuntimeError(
                f"[OfflineData] fact dates are unavailable; "
                f"missing_dates={missing_dates}"
            )
        return context

    def _ingest_sources(self, trade_date: str) -> bool:
        logs.info(f"fact ingest started; trade_date={trade_date}")
        available_payload = False
        missing_payloads = 0

        for source_name, source in self._sources.items():
            plan = DownloadPlan(
                source_name=source_name,
                trade_date=trade_date,
                broker=source.broker,
                raw_object=source.raw_object,
            )
            meta_path = self._path_manager.raw_meta(
                broker=plan.broker,
                trade_date=plan.trade_date,
                source_name=plan.source_name,
            )
            if meta.find(pm=self._path_manager, meta_path=meta_path) is not None:
                available_payload = True
                logs.info(
                    f"raw meta hit; source={plan.source_name} "
                    f"trade_date={trade_date} output={meta_path}"
                )
                continue

            adapter = self._adapter_cache.get(plan.broker)
            if adapter is None:
                adapter = self._broker_classes[plan.broker](app_cfg=self._app_config)
                self._adapter_cache[plan.broker] = adapter

            fetched = adapter.fetch(record=plan, pm=self._path_manager)
            if fetched is None:
                logs.warning(
                    f"source unavailable; source={plan.source_name} "
                    f"broker={plan.broker} trade_date={trade_date}"
                )
                missing_payloads += 1
                continue

            output_file = self._path_manager.raw_payload(
                broker=fetched.broker,
                trade_date=fetched.trade_date,
                source_name=fetched.source_name,
                payload_file=fetched.payload_file,
            )
            meta.commit(pm=self._path_manager, payload_path=output_file)
            available_payload = True

        logs.info(f"fact ingest finished; trade_date={trade_date}")
        if not available_payload:
            return False
        if missing_payloads:
            raise RuntimeError(
                f"source payloads are only partially available; "
                f"trade_date={trade_date} missing_count={missing_payloads}"
            )
        return True

    def _normalize_sources(self, trade_date: str) -> None:
        logs.info(f"fact normalize started; trade_date={trade_date}")
        for plan in self._normalize_plans:
            processed_meta = self._path_manager.processed_meta(
                dataset_name=plan.output,
                version=self._processed_version,
                trade_date=trade_date,
            )
            output_file = self._path_manager.processed_data(
                dataset_name=plan.output,
                version=self._processed_version,
                trade_date=trade_date,
            )
            loaded_output = meta.find(
                pm=self._path_manager,
                meta_path=processed_meta,
                expected_payload_path=output_file,
            )
            if (
                plan.output in _LEVEL2_TRADE_OUTPUTS
                and loaded_output is not None
                and loaded_output.symbol_slices is None
            ):
                raise RuntimeError(
                    f"Level-2 Meta has no symbol_slices: "
                    f"dataset={plan.output}, meta_path={processed_meta}"
                )
            if loaded_output is not None:
                logs.info(
                    f"processed meta hit; target={plan.output} "
                    f"source={plan.source_name} trade_date={trade_date}"
                )
                continue

            raw_meta_path = self._path_manager.raw_meta(
                broker=plan.source.broker,
                source_name=plan.source_name,
                trade_date=trade_date,
            )
            loaded_input = meta.require(
                pm=self._path_manager,
                meta_path=raw_meta_path,
            )
            staging_candidate = self._path_manager.staging_payload(
                broker=plan.source.broker,
                source_name=plan.source_name,
                trade_date=trade_date,
                payload_file=loaded_input.payload_path.name,
            )
            input_file = loaded_input.payload_path
            if (
                staging_candidate.is_file()
                and staging_candidate.stat().st_size == loaded_input.size_bytes
            ):
                input_file = staging_candidate

            normalized = plan.operation(
                input_file=input_file,
                raw_object=plan.source.raw_object,
                target_name=plan.output,
                output_name=output_file,
                trade_date=trade_date,
            )
            if plan.output not in _EMPTY_EVENT_OUTPUTS:
                table_ops.require_nonempty(
                    normalized.table,
                    who=(
                        f"FactNormalize source={plan.source_name} "
                        f"target={plan.output} trade_date={trade_date}"
                    ),
                )
            write_parquet_atomic(
                output_file=output_file,
                table=normalized.table,
            )
            meta.commit(
                pm=self._path_manager,
                payload_path=output_file,
                upstream_meta_path=raw_meta_path,
                symbol_slices=normalized.symbol_slices,
            )
        logs.info(f"fact normalize finished; trade_date={trade_date}")
