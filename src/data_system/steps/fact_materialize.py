# filepath: src/data_system/steps/fact_materialize.py
"""Materialize selected fact sources over formal trade dates."""

from __future__ import annotations

import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field

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

_EMPTY_PROCESSED_OUTPUTS = frozenset(
    {"stock_basic", "stock_st", "suspend_d"}
)
_LEVEL2_TRADE_OUTPUTS = frozenset({"sh_trade", "sz_trade"})


@dataclass(frozen=True, slots=True)
class _NormalizePlan:
    source_name: str
    source: SourceConfig
    output: str
    operation: NormalizeOperation


@dataclass(slots=True)
class _OperationTiming:
    total_seconds: float = 0.0
    runs: int = 0

    def add(self, elapsed_seconds: float) -> None:
        self.total_seconds += elapsed_seconds
        self.runs += 1


@dataclass(slots=True)
class _MaterializeStats:
    raw_reused: int = 0
    raw_fetched: int = 0
    unavailable: int = 0
    processed_reused: int = 0
    processed_published: int = 0
    raw_ingest_timings: dict[str, _OperationTiming] = field(default_factory=dict)
    normalize_timings: dict[str, _OperationTiming] = field(default_factory=dict)


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
        stats = _MaterializeStats()
        for trade_date in context.trade_dates:
            date_started_at = time.perf_counter()
            if not self._ingest_sources(trade_date, stats):
                missing_dates.append(trade_date)
                continue
            self._normalize_sources(trade_date, stats)
            logs.info(
                f"✅ fact date; trade_date={trade_date} "
                f"elapsed_seconds={time.perf_counter() - date_started_at:.3f}"
            )

        if missing_dates:
            raise RuntimeError(
                f"[OfflineData] fact dates are unavailable; "
                f"missing_dates={missing_dates}"
            )
        self._log_operation_summary(stats)
        logs.info(
            f"✅ fact materialize; trade_dates={len(context.trade_dates)} "
            f"raw_reused={stats.raw_reused} raw_fetched={stats.raw_fetched} "
            f"processed_reused={stats.processed_reused} "
            f"processed_published={stats.processed_published} "
            f"unavailable={stats.unavailable}"
        )
        return context

    def _ingest_sources(
        self,
        trade_date: str,
        stats: _MaterializeStats,
    ) -> bool:
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
                stats.raw_reused += 1
                logs.info(
                    f"♻️ raw meta hit; source={plan.source_name} "
                    f"broker={plan.broker} trade_date={trade_date} "
                    f"meta={meta_path}"
                )
                continue

            adapter = self._adapter_cache.get(plan.broker)
            if adapter is None:
                adapter = self._broker_classes[plan.broker](app_cfg=self._app_config)
                self._adapter_cache[plan.broker] = adapter

            ingest_started_at = time.perf_counter()
            fetched = adapter.fetch(record=plan, pm=self._path_manager)
            if fetched is None:
                elapsed_seconds = time.perf_counter() - ingest_started_at
                logs.warning(
                    f"⚠️ fact source; reason=unavailable "
                    f"source={plan.source_name} broker={plan.broker} "
                    f"trade_date={trade_date} "
                    f"elapsed_seconds={elapsed_seconds:.3f}"
                )
                missing_payloads += 1
                stats.unavailable += 1
                continue

            output_file = self._path_manager.raw_payload(
                broker=fetched.broker,
                trade_date=fetched.trade_date,
                source_name=fetched.source_name,
                payload_file=fetched.payload_file,
            )
            meta.commit(pm=self._path_manager, payload_path=output_file)
            elapsed_seconds = time.perf_counter() - ingest_started_at
            timing = stats.raw_ingest_timings.setdefault(
                plan.source_name,
                _OperationTiming(),
            )
            timing.add(elapsed_seconds)
            available_payload = True
            stats.raw_fetched += 1
            logs.info(
                f"✅ raw ingest; source={plan.source_name} broker={plan.broker} "
                f"trade_date={trade_date} elapsed_seconds={elapsed_seconds:.3f} "
                f"output={output_file}"
            )

        if not available_payload:
            return False
        if missing_payloads:
            raise RuntimeError(
                f"source payloads are only partially available; "
                f"trade_date={trade_date} missing_count={missing_payloads}"
            )
        return True

    def _normalize_sources(
        self,
        trade_date: str,
        stats: _MaterializeStats,
    ) -> None:
        for plan in self._normalize_plans:
            processed_paths = self._path_manager.processed_object(
                dataset_name=plan.output,
                version=self._processed_version,
                trade_date=trade_date,
            )
            loaded_output = meta.find(
                pm=self._path_manager,
                meta_path=processed_paths.meta_path,
                expected_payload_path=processed_paths.payload_path,
            )
            if (
                plan.output in _LEVEL2_TRADE_OUTPUTS
                and loaded_output is not None
                and loaded_output.symbol_slices is None
            ):
                raise RuntimeError(
                    f"Level-2 Meta has no symbol_slices: "
                    f"dataset={plan.output}, meta_path={processed_paths.meta_path}"
                )
            if loaded_output is not None:
                stats.processed_reused += 1
                logs.info(
                    f"♻️ processed meta hit; target={plan.output} "
                    f"source={plan.source_name} trade_date={trade_date} "
                    f"meta={processed_paths.meta_path}"
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

            normalize_started_at = time.perf_counter()
            normalized = plan.operation(
                input_file=input_file,
                raw_object=plan.source.raw_object,
                target_name=plan.output,
                output_name=processed_paths.payload_path,
                trade_date=trade_date,
            )
            if plan.output not in _EMPTY_PROCESSED_OUTPUTS:
                table_ops.require_nonempty(
                    normalized.table,
                    who=(
                        f"FactNormalize source={plan.source_name} "
                        f"target={plan.output} trade_date={trade_date}"
                    ),
                )
            normalize_seconds = time.perf_counter() - normalize_started_at
            timing = stats.normalize_timings.setdefault(
                plan.output,
                _OperationTiming(),
            )
            timing.add(normalize_seconds)
            write_parquet_atomic(
                output_file=processed_paths.payload_path,
                table=normalized.table,
            )
            meta.commit(
                pm=self._path_manager,
                payload_path=processed_paths.payload_path,
                upstream_meta_path=raw_meta_path,
                symbol_slices=normalized.symbol_slices,
            )
            stats.processed_published += 1
            logs.info(
                f"✅ processed publish; target={plan.output} "
                f"source={plan.source_name} trade_date={trade_date} "
                f"rows={normalized.table.num_rows} "
                f"normalize_seconds={normalize_seconds:.3f} "
                f"output={processed_paths.payload_path}"
            )

    def _log_operation_summary(self, stats: _MaterializeStats) -> None:
        rows: list[str] = []
        for source_name, timing in stats.raw_ingest_timings.items():
            average_seconds = timing.total_seconds / timing.runs
            label = f"raw ingest {source_name}"
            rows.append(
                f"{label:<35} {timing.total_seconds:>8.3f}s "
                f"avg={average_seconds:.3f}s runs={timing.runs}"
            )
        for target_name, timing in stats.normalize_timings.items():
            average_seconds = timing.total_seconds / timing.runs
            label = f"normalize {target_name}"
            rows.append(
                f"{label:<35} {timing.total_seconds:>8.3f}s "
                f"avg={average_seconds:.3f}s runs={timing.runs}"
            )
        if rows:
            summary = "\n".join(rows)
            logs.info(f"✅ ===== Fact operation summary =====\n{summary}")
