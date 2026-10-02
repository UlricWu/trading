# filepath: src/data_system/level2_cache.py
"""Coordinate Level-2 raw cache writers and size-confirmed eviction."""

from __future__ import annotations

import fcntl
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

from src import logs
from src.access import meta
from src.config.data_config import SourceConfig
from src.data_system.brokers.level2 import Level2Broker
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@contextmanager
def level2_cache_lock(pm: PathManager) -> Iterator[None]:
    """Exclude concurrent Level-2 processing, migration and cache deletion.

    Example:
        with level2_cache_lock(pm):
            step.run(context)
    """
    root = FileSystem.ensure_dir(pm.level2_cache_dir())
    with (root / ".cache.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Level-2 raw cache is in use by another writer") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def completed_trade_date(
    *, pm: PathManager, sources: Mapping[str, SourceConfig],
    processed_version: str, trade_date: str,
) -> bool:
    """Verify all selected trade objects before raw cache becomes disposable.

    Example:
        complete = completed_trade_date(
            pm=pm, sources=sources, processed_version="v1", trade_date="2026-09-21",
        )
    """
    for source_name, source in sources.items():
        for output in source.outputs:
            paths = pm.processed_object(
                dataset_name=output, version=processed_version, trade_date=trade_date,
            )
            record = meta.find(pm=pm, meta_path=paths.meta_path,
                               expected_payload_path=paths.payload_path)
            if record is None:
                return False
            if record.symbol_slices is None:
                raise RuntimeError(f"Level-2 Meta has no symbol_slices: {paths.meta_path}")
            raw_meta = pm.raw_meta(
                broker=source.broker, source_name=source_name, trade_date=trade_date,
            )
            expected_upstream = raw_meta.relative_to(pm.storage_root).as_posix()
            if record.upstream is None or record.upstream[0].as_posix() != expected_upstream:
                raise RuntimeError(f"Level-2 Meta has an unexpected raw upstream: {paths.meta_path}")
    return True


def recent_complete_dates(
    *, pm: PathManager, sources: Mapping[str, SourceConfig],
    processed_version: str, keep_days: int,
) -> frozenset[str]:
    """Find the newest completed trading partitions, independent of calendar days.

    Example:
        keep = recent_complete_dates(
            pm=pm, sources=sources, processed_version="v1", keep_days=5,
        )
    """
    outputs = [output for source in sources.values() for output in source.outputs]
    if not outputs:
        return frozenset()
    root = pm.processed_version_dir(dataset_name=outputs[0], version=processed_version)
    dates = sorted(
        (path.parent.name.removeprefix("trade_date=")
         for path in root.glob("trade_date=*/meta.json")), reverse=True,
    )
    kept: set[str] = set()
    for trade_date in dates:
        if completed_trade_date(
            pm=pm, sources=sources, processed_version=processed_version,
            trade_date=trade_date,
        ):
            kept.add(trade_date)
            if len(kept) == keep_days:
                break
    return frozenset(kept)


def prune_level2_cache(
    *, pm: PathManager, sources: Mapping[str, SourceConfig],
    processed_version: str, keep_days: int, get_broker: Callable[[], Level2Broker],
) -> int:
    """Delete completed old caches only after rechecking the cloud byte count.

    The caller holds ``level2_cache_lock``. Partial downloads are never deleted.

    Example:
        removed = prune_level2_cache(
            pm=pm, sources=sources, processed_version="v1", keep_days=5,
            get_broker=get_broker,
        )
    """
    kept = recent_complete_dates(
        pm=pm, sources=sources, processed_version=processed_version, keep_days=keep_days,
    )
    removed = 0
    for source_name, source in sources.items():
        if not source.outputs:
            continue
        for cache in sorted((pm.level2_cache_dir() / source_name).glob(
            f"trade_date=*/{source.raw_object}.csv.7z"
        )):
            trade_date = cache.parent.name.removeprefix("trade_date=")
            if trade_date in kept or not completed_trade_date(
                pm=pm, sources=sources, processed_version=processed_version,
                trade_date=trade_date,
            ):
                continue
            raw_meta = pm.raw_meta(
                broker=source.broker, source_name=source_name, trade_date=trade_date,
            )
            record = meta.find_level2_raw(pm=pm, meta_path=raw_meta)
            if not isinstance(record, meta.RemoteRawRecord):
                continue
            if cache.is_symlink() or FileSystem.get_file_size(cache) != record.size_bytes:
                logs.warning(f"⚠️ Level-2 cache retained; reason=size_or_type file={cache}")
                continue
            if get_broker().describe(record.remote_path) != record:
                logs.warning(f"⚠️ Level-2 cache retained; reason=cloud_mismatch file={cache}")
                continue
            cache.unlink()
            removed += 1
            logs.info(f"✅ Level-2 cache removed; file={cache}")
    return removed
