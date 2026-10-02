# filepath: scripts/migrate_level2_archive.py
"""Match local Level-2 raw to Baidu Netdisk before changing Meta or reclaiming space.

Example:
    uv run python -m scripts.migrate_level2_archive --storage-root /absolute/data
    uv run python -m scripts.migrate_level2_archive --storage-root /absolute/data --apply --cleanup
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from src.access import meta
from src.config.app_config import AppConfig
from src.config.data_config import SourceConfig
from src.data_system.brokers.level2 import Level2Broker
from src.data_system.level2_cache import (
    completed_trade_date,
    level2_cache_lock,
    recent_complete_dates,
)
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class _Candidate:
    meta_path: Path
    source_name: str
    trade_date: str
    archive: meta.RemoteRawRecord
    needs_conversion: bool
    can_cleanup: bool


@dataclass(frozen=True, slots=True)
class _ReportRow:
    source: str
    trade_date: str
    status: str
    size_bytes: int = 0
    remote_path: str | None = None
    cleanup: str = "retain"
    local_raw_bytes: int = 0
    cache_bytes: int = 0
    reclaimable_bytes: int = 0


def main(argv: Sequence[str] | None = None) -> int:
    """Preflight by default; explicit apply and cleanup flags enable local mutations.

    Example:
        exit_code = main(["--storage-root", "/absolute/data", "--start", "2026-09-01"])
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-root", required=True, type=Path)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--report", type=Path, help="Write the preflight inventory as JSON.")
    args = parser.parse_args(argv)
    if args.cleanup and not args.apply:
        parser.error("--cleanup requires --apply")
    for value in (args.start, args.end):
        if value is not None:
            DateTimeUtils.require_system_date(value, field_name="date bound")
    if args.start and args.end and args.start > args.end:
        parser.error("--start must not be later than --end")
    app_config = AppConfig.load()
    pm = PathManager(args.storage_root)
    broker = Level2Broker(app_cfg=app_config)
    sources = app_config.data.sources
    active = {name: source for name, source in sources.items() if source.enabled}
    keep_days = app_config.data.brokers[Level2Broker.name].raw_cache_days
    assert keep_days is not None
    rows: list[_ReportRow] = []
    candidates: list[_Candidate] = []
    with level2_cache_lock(pm):
        kept = recent_complete_dates(
            pm=pm, sources=active, processed_version="v1", keep_days=keep_days,
        )
        for raw_meta in sorted((pm.storage_root / "raw" / Level2Broker.name).glob(
            "*/trade_date=*/meta.json"
        )):
            if raw_meta.is_symlink() or raw_meta.resolve() != raw_meta:
                raise RuntimeError(f"migration path must not traverse symlinks: {raw_meta}")
            source_name = raw_meta.parent.parent.name
            trade_date = raw_meta.parent.name.removeprefix("trade_date=")
            DateTimeUtils.require_system_date(trade_date, field_name="raw trade_date")
            if (args.start and trade_date < args.start) or (args.end and trade_date > args.end):
                continue
            if source_name not in sources:
                rows.append(_ReportRow(source_name, trade_date, "unconfigured"))
                continue
            source = sources[source_name]
            record = meta.find_level2_raw(pm=pm, meta_path=raw_meta)
            assert record is not None
            if isinstance(record, meta.RemoteRawRecord):
                archive = broker.describe(record.remote_path)
                if archive != record:
                    rows.append(_ReportRow(
                        source_name, trade_date, "cloud_missing_or_size_mismatch", record.size_bytes,
                    ))
                    continue
                convert = False
            else:
                if (
                    record.payload_path.name != f"{source.raw_object}.csv.7z"
                    or record.upstream is not None or record.symbol_slices is not None
                ):
                    raise RuntimeError(f"invalid Level-2 local raw identity: {raw_meta}")
                archive = broker.locate(
                    raw_object=source.raw_object, trade_date=trade_date,
                    expected_size=record.size_bytes,
                )
                if archive is None:
                    rows.append(_ReportRow(
                        source_name, trade_date, "cloud_missing_or_size_mismatch", record.size_bytes,
                    ))
                    continue
                convert = True
            complete = not source.outputs or completed_trade_date(
                pm=pm, sources=active | {source_name: source},
                processed_version="v1", trade_date=trade_date,
            )
            cleanup = "retain_incomplete"
            if complete:
                cleanup = "cache_recent" if source.outputs and trade_date in kept else "remove_local"
            raw = raw_meta.parent / archive.payload
            cache = pm.staging_payload(
                broker=source.broker, source_name=source_name,
                trade_date=trade_date, payload_file=archive.payload,
            )
            raw_bytes = FileSystem.get_file_size(raw) if raw.exists() else 0
            cache_bytes = FileSystem.get_file_size(cache) if cache.exists() else 0
            can_cleanup = complete
            if (
                raw.is_symlink() or cache.is_symlink()
                or (raw.exists() and raw_bytes != archive.size_bytes)
                or (cache.exists() and cache_bytes != archive.size_bytes)
            ):
                cleanup = "retain_local_mismatch"
                can_cleanup = False
            reclaimable = 0
            if cleanup == "remove_local":
                reclaimable = raw_bytes + cache_bytes
            elif cleanup == "cache_recent" and cache_bytes:
                reclaimable = raw_bytes
            rows.append(_ReportRow(
                source_name, trade_date, "matched" if convert else "archived",
                archive.size_bytes, archive.remote_path, cleanup,
                raw_bytes, cache_bytes, reclaimable,
            ))
            candidates.append(_Candidate(
                raw_meta, source_name, trade_date, archive, convert, can_cleanup,
            ))

        report = {
            "storage_root": str(pm.storage_root), "start": args.start, "end": args.end,
            "apply": args.apply, "cleanup": args.cleanup, "keep_dates": sorted(kept),
            "reclaimable_bytes": sum(row.reclaimable_bytes for row in rows),
            "partitions": [asdict(row) for row in rows],
        }
        if args.report is not None:
            FileSystem.write_bytes_atomic(
                args.report, json.dumps(report, indent=2, ensure_ascii=False).encode(),
            )
        for row in rows:
            print(
                f"{row.trade_date} {row.source}: {row.status} "
                f"size_bytes={row.size_bytes} cleanup={row.cleanup}"
            )
        print(
            f"partitions={len(rows)} matched={len(candidates)} "
            f"retained_unmatched={len(rows) - len(candidates)} "
            f"reclaimable_bytes={report['reclaimable_bytes']} "
            f"apply={args.apply} cleanup={args.cleanup}"
        )
        if args.apply:
            for candidate in candidates:
                _apply_candidate(
                    pm=pm, broker=broker, candidate=candidate,
                    source=sources[candidate.source_name], keep_dates=kept, cleanup=args.cleanup,
                )
    return 0


def _apply_candidate(
    *, pm: PathManager, broker: Level2Broker, candidate: _Candidate,
    source: SourceConfig, keep_dates: frozenset[str], cleanup: bool,
) -> None:
    archive = candidate.archive
    if broker.describe(archive.remote_path) != archive:
        raise RuntimeError(f"cloud raw changed after preflight: {archive.remote_path}")
    if not candidate.needs_conversion and (not cleanup or not candidate.can_cleanup):
        return
    local_raw = candidate.meta_path.parent / archive.payload
    if local_raw.is_symlink() or (
        local_raw.exists() and FileSystem.get_file_size(local_raw) != archive.size_bytes
    ):
        raise RuntimeError(f"local raw changed after preflight: {local_raw}")
    if candidate.needs_conversion:
        existing = meta.require(pm=pm, meta_path=candidate.meta_path, expected_payload_path=local_raw)
        if existing.size_bytes != archive.size_bytes:
            raise RuntimeError(f"local raw changed after preflight: {local_raw}")
        backup = (
            pm.level2_cache_dir() / ".migration-meta" / candidate.source_name
            / candidate.meta_path.parent.name / "meta.json"
        )
        original = candidate.meta_path.read_bytes()
        if backup.exists() and backup.read_bytes() != original:
            raise RuntimeError(f"different migration Meta backup already exists: {backup}")
        if not backup.exists():
            FileSystem.write_bytes_atomic(backup, original)
        meta.commit_remote_raw(pm=pm, meta_path=candidate.meta_path, record=archive)
    if not cleanup or not candidate.can_cleanup:
        return
    cache = pm.staging_payload(
        broker=source.broker, source_name=candidate.source_name,
        trade_date=candidate.trade_date, payload_file=archive.payload,
    )
    keep_cache = bool(source.outputs) and candidate.trade_date in keep_dates
    if cache.is_symlink() or (cache.exists() and FileSystem.get_file_size(cache) != archive.size_bytes):
        raise RuntimeError(f"cache size or type mismatch; files retained: {cache}")
    if keep_cache and local_raw.exists() and not cache.exists():
        FileSystem.ensure_dir(cache.parent)
        if local_raw.stat().st_dev == cache.parent.stat().st_dev:
            os.replace(local_raw, cache)
        else:
            FileSystem.copy_file_atomic(local_raw, cache)
            local_raw.unlink()
    elif local_raw.exists():
        local_raw.unlink()
    if not keep_cache and cache.exists():
        cache.unlink()
    print(f"applied {candidate.trade_date} {candidate.source_name}: cache_kept={keep_cache}")


if __name__ == "__main__":
    sys.exit(main())
