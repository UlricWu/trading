# filepath: src/data_system/steps/fact_materialize.py
"""Materialize selected fact sources over formal trade dates."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from src import logs
from src.access import meta
from src.config.data_config import SourceConfig
from src.data_system.brokers.base import BrokerAdapter
from src.data_system.context import DataContext
from src.data_system.normalize import NormalizeOperation
from src.data_system.steps._partition import _publish_parquet_object
from src.utils import table_ops
from src.utils.path import PathManager

_EMPTY_PROCESSED_OUTPUTS = frozenset({"stock_basic", "stock_st", "suspend_d"})
_LEVEL2_TRADE_OUTPUTS = frozenset({"sh_trade", "sz_trade"})


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
    """Materialize prepared facts from one workflow-bound broker.

    The workflow supplies nonempty sources and their matching broker and normalizer.
    The workflow supplies a lazy get_broker callable, used only on raw Meta misses.

    Example:
        step = FactMaterializeStep(
            path_manager=path_manager,
            sources=fact_sources,
            get_broker=get_broker,
            normalize_operation=normalize_operation,
            processed_version="v1",
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
        path_manager: PathManager,
        sources: Mapping[str, SourceConfig],
        get_broker: Callable[[], BrokerAdapter],
        normalize_operation: NormalizeOperation,
        processed_version: str,
    ) -> None:
        """Bind prepared fact dependencies and lazy broker access.

        Example:
            step = FactMaterializeStep(
                path_manager=path_manager,
                sources=fact_sources,
                get_broker=get_broker,
                normalize_operation=normalize_operation,
                processed_version="v1",
            )
        """
        self._path_manager = path_manager
        self._sources = dict(sources)
        self._get_broker = get_broker
        self._normalize_operation = normalize_operation
        self._processed_version = processed_version

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
            raw_inputs = self._ingest_sources(trade_date, stats)
            if not raw_inputs:
                missing_dates.append(trade_date)
                continue
            self._normalize_sources(trade_date, raw_inputs, stats)
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
    ) -> dict[str, meta.MetaRecord]:
        raw_inputs: dict[str, meta.MetaRecord] = {}
        missing_payloads = 0

        for source_name, source in self._sources.items():
            meta_path = self._path_manager.raw_meta(
                broker=source.broker,
                trade_date=trade_date,
                source_name=source_name,
            )
            loaded_input = meta.find(pm=self._path_manager, meta_path=meta_path)
            if loaded_input is not None:
                raw_inputs[source_name] = loaded_input
                stats.raw_reused += 1
                logs.info(
                    f"♻️ raw meta hit; source={source_name} "
                    f"broker={source.broker} trade_date={trade_date} "
                    f"meta={meta_path}"
                )
                continue

            adapter = self._get_broker()
            ingest_started_at = time.perf_counter()
            output_file = adapter.fetch(
                source_name=source_name,
                raw_object=source.raw_object,
                trade_date=trade_date,
                pm=self._path_manager,
            )
            if output_file is None:
                elapsed_seconds = time.perf_counter() - ingest_started_at
                logs.warning(
                    f"⚠️ fact source; reason=unavailable "
                    f"source={source_name} broker={source.broker} "
                    f"trade_date={trade_date} "
                    f"elapsed_seconds={elapsed_seconds:.3f}"
                )
                missing_payloads += 1
                stats.unavailable += 1
                continue

            meta.commit(pm=self._path_manager, payload_path=output_file)
            elapsed_seconds = time.perf_counter() - ingest_started_at
            timing = stats.raw_ingest_timings.setdefault(
                source_name,
                _OperationTiming(),
            )
            timing.add(elapsed_seconds)
            raw_inputs[source_name] = meta.require(
                pm=self._path_manager,
                meta_path=meta_path,
            )
            stats.raw_fetched += 1
            logs.info(
                f"✅ raw ingest; source={source_name} broker={source.broker} "
                f"trade_date={trade_date} elapsed_seconds={elapsed_seconds:.3f} "
                f"output={output_file}"
            )

        if raw_inputs and missing_payloads:
            raise RuntimeError(
                f"source payloads are only partially available; "
                f"trade_date={trade_date} missing_count={missing_payloads}"
            )
        return raw_inputs

    def _normalize_sources(
        self,
        trade_date: str,
        raw_inputs: Mapping[str, meta.MetaRecord],
        stats: _MaterializeStats,
    ) -> None:
        for source_name, source in self._sources.items():
            input_file: Path | None = None
            for output in source.outputs:
                processed_paths = self._path_manager.processed_object(
                    dataset_name=output,
                    version=self._processed_version,
                    trade_date=trade_date,
                )
                loaded_output = meta.find(
                    pm=self._path_manager,
                    meta_path=processed_paths.meta_path,
                    expected_payload_path=processed_paths.payload_path,
                )
                if (
                    output in _LEVEL2_TRADE_OUTPUTS
                    and loaded_output is not None
                    and loaded_output.symbol_slices is None
                ):
                    raise RuntimeError(
                        f"Level-2 Meta has no symbol_slices: "
                        f"dataset={output}, meta_path={processed_paths.meta_path}"
                    )
                if loaded_output is not None:
                    stats.processed_reused += 1
                    logs.info(
                        f"♻️ processed meta hit; target={output} "
                        f"source={source_name} trade_date={trade_date} "
                        f"meta={processed_paths.meta_path}"
                    )
                    continue

                raw_meta_path = self._path_manager.raw_meta(
                    broker=source.broker,
                    source_name=source_name,
                    trade_date=trade_date,
                )
                if input_file is None:
                    loaded_input = raw_inputs[source_name]
                    staging_candidate = self._path_manager.staging_payload(
                        broker=source.broker,
                        source_name=source_name,
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
                normalized = self._normalize_operation(
                    input_file=input_file,
                    raw_object=source.raw_object,
                    target_name=output,
                    output_name=processed_paths.payload_path,
                    trade_date=trade_date,
                )
                if output not in _EMPTY_PROCESSED_OUTPUTS:
                    table_ops.require_nonempty(
                        normalized.table,
                        who=(
                            f"FactNormalize source={source_name} "
                            f"target={output} trade_date={trade_date}"
                        ),
                    )
                normalize_seconds = time.perf_counter() - normalize_started_at
                timing = stats.normalize_timings.setdefault(
                    output,
                    _OperationTiming(),
                )
                timing.add(normalize_seconds)
                _publish_parquet_object(
                    pm=self._path_manager,
                    paths=processed_paths,
                    table=normalized.table,
                    upstream_meta_path=raw_meta_path,
                    symbol_slices=normalized.symbol_slices,
                )
                stats.processed_published += 1
                logs.info(
                    f"✅ processed publish; target={output} "
                    f"source={source_name} trade_date={trade_date} "
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
