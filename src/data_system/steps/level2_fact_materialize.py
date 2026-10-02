# filepath: src/data_system/steps/level2_fact_materialize.py
"""Materialize missing Level-2 trade facts from compressed cloud raw."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from src import logs
from src.access import meta
from src.config.data_config import SourceConfig
from src.data_system.brokers.level2 import Level2Broker
from src.data_system.context import DataContext
from src.data_system.level2_cache import level2_cache_lock, prune_level2_cache
from src.data_system.normalize import NormalizeOperation
from src.data_system.steps._partition import _publish_parquet_object
from src.utils import table_ops
from src.utils.path import ObjectPaths, PathManager


class Level2FactMaterializeStep:
    """Reuse processed facts before preparing any Level-2 raw input.

    Example:
        step = Level2FactMaterializeStep(
            path_manager=pm, sources=sources, get_broker=get_broker,
            normalize_operation=normalize_level2, processed_version="v1", raw_cache_days=5,
        )
    """

    def __init__(
        self, *, path_manager: PathManager, sources: Mapping[str, SourceConfig],
        get_broker: Callable[[], Level2Broker], normalize_operation: NormalizeOperation,
        processed_version: str, raw_cache_days: int,
    ) -> None:
        """Bind the Level-2 source family and its lazy cloud client.

        Example:
            step = Level2FactMaterializeStep(
                path_manager=pm, sources=sources, get_broker=get_broker,
                normalize_operation=normalize_level2, processed_version="v1", raw_cache_days=5,
            )
        """
        self._pm = path_manager
        self._sources = dict(sources)
        self._get_broker = get_broker
        self._normalize = normalize_operation
        self._version = processed_version
        self._cache_days = raw_cache_days

    def run(self, context: DataContext) -> DataContext:
        """Publish missing facts, retaining successful partitions on later failure.

        Example:
            context = step.run(DataContext(
                start="2026-09-21", end="2026-09-21", trade_dates=("2026-09-21",),
            ))
        """
        missing: list[str] = []
        with level2_cache_lock(self._pm):
            for trade_date in context.trade_dates:
                published = 0
                missing_before = len(missing)
                for source_name, source in self._sources.items():
                    pending: list[tuple[str, ObjectPaths]] = []
                    for output in source.outputs:
                        paths = self._pm.processed_object(
                            dataset_name=output, version=self._version, trade_date=trade_date,
                        )
                        existing = meta.find(
                            pm=self._pm, meta_path=paths.meta_path,
                            expected_payload_path=paths.payload_path,
                        )
                        if existing is None:
                            pending.append((output, paths))
                        elif existing.symbol_slices is None:
                            raise RuntimeError(f"Level-2 Meta has no symbol_slices: {paths.meta_path}")
                        else:
                            logs.info(f"♻️ processed meta hit; target={output} trade_date={trade_date}")
                    if not pending:
                        continue
                    raw_meta = self._pm.raw_meta(
                        broker=source.broker, source_name=source_name, trade_date=trade_date,
                    )
                    record = meta.find_level2_raw(pm=self._pm, meta_path=raw_meta)
                    if record is None:
                        record = self._get_broker().locate(
                            raw_object=source.raw_object, trade_date=trade_date,
                        )
                        if record is None:
                            missing.append(f"{source_name}@{trade_date}")
                            continue
                        meta.commit_remote_raw(pm=self._pm, meta_path=raw_meta, record=record)
                    input_file: Path
                    if isinstance(record, meta.MetaRecord):
                        input_file = record.payload_path
                    else:
                        cache = self._pm.staging_payload(
                            broker=source.broker, source_name=source_name,
                            trade_date=trade_date, payload_file=record.payload,
                        )
                        retained_raw = raw_meta.parent / record.payload
                        if (
                            cache.is_file() and not cache.is_symlink()
                            and cache.stat().st_size == record.size_bytes
                        ):
                            input_file = cache
                        elif (
                            retained_raw.is_file() and not retained_raw.is_symlink()
                            and retained_raw.stat().st_size == record.size_bytes
                        ):
                            input_file = retained_raw
                        else:
                            input_file = self._get_broker().download(record=record, destination=cache)
                    for output, paths in pending:
                        normalized = self._normalize(
                            input_file=input_file, raw_object=source.raw_object,
                            target_name=output, output_name=paths.payload_path, trade_date=trade_date,
                        )
                        table_ops.require_nonempty(
                            normalized.table, who=f"Level2Normalize {output}@{trade_date}",
                        )
                        if normalized.symbol_slices is None:
                            raise RuntimeError(
                                f"Level-2 Normalize has no symbol_slices: {output}@{trade_date}"
                            )
                        _publish_parquet_object(
                            pm=self._pm, paths=paths, table=normalized.table,
                            upstream_meta_path=raw_meta, symbol_slices=normalized.symbol_slices,
                        )
                        published += 1
                        logs.info(
                            f"✅ processed publish; target={output} "
                            f"trade_date={trade_date} rows={normalized.table.num_rows}"
                        )
                        del normalized
                if published and len(missing) == missing_before:
                    prune_level2_cache(
                        pm=self._pm, sources=self._sources, processed_version=self._version,
                        keep_days=self._cache_days, get_broker=self._get_broker,
                    )
        if missing:
            raise RuntimeError(f"Level-2 fact sources unavailable: {missing}")
        return context
