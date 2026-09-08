# filepath: tests/scripts/test_migrate_raw_meta.py
"""Raw Meta migration command tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _write_v1_raw_object(
    *,
    storage_root: Path,
    source_name: str,
    partition: str,
    payload_name: str = "data.parquet",
    legacy_raw_identity: str | None = None,
) -> tuple[Path, bytes]:
    partition_dir = (
        storage_root / "raw" / "tushare" / source_name / partition
    )
    partition_dir.mkdir(parents=True)
    payload = b"verified payload"
    payload_path = partition_dir / payload_name
    payload_path.write_bytes(payload)
    raw_identity = legacy_raw_identity or (
        f"raw/tushare/{source_name}/{partition}/{payload_name}"
    )
    legacy_record = {
        "extra": {},
        "output": {
            "fingerprint": {"size": len(payload)},
            "path": f"/legacy/storage/{raw_identity}",
        },
        "upstreams": [],
        "version": "V1.0",
    }
    meta_path = partition_dir / "meta.json"
    meta_path.write_text(
        json.dumps(legacy_record, indent=2),
        encoding="utf-8",
    )
    return meta_path, payload


def _run_migration(
    *,
    storage_root: Path,
    should_apply: bool = False,
) -> subprocess.CompletedProcess[str]:
    arguments = [
        sys.executable,
        "-m",
        "scripts.migrate_raw_meta",
        "--storage-root",
        str(storage_root),
    ]
    if should_apply:
        arguments.append("--apply")
    return subprocess.run(
        arguments,
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_dry_run_accepts_v1_identity_after_storage_root_move(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "current-storage"
    storage_root.mkdir()
    meta_path, _ = _write_v1_raw_object(
        storage_root=storage_root,
        source_name="daily_bar",
        partition="trade_date=2026-07-15",
    )
    original_meta = meta_path.read_bytes()

    completed = _run_migration(storage_root=storage_root)

    assert completed.returncode == 0, completed.stderr
    assert "MIGRATABLE meta_path=raw/tushare/daily_bar/" in completed.stdout
    assert (
        "SUMMARY mode=dry-run current=0 migratable=1 migrated=0 blocked=0"
        in completed.stdout
    )
    assert meta_path.read_bytes() == original_meta


def test_apply_migrates_v1_daily_calendar_meta_without_moving_payload(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "current-storage"
    storage_root.mkdir()
    meta_path, payload = _write_v1_raw_object(
        storage_root=storage_root,
        source_name="trade_calendar",
        partition="trade_date=2026-07-15",
    )

    completed = _run_migration(
        storage_root=storage_root,
        should_apply=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert (
        "SUMMARY mode=apply current=0 migratable=1 migrated=1 blocked=0"
        in completed.stdout
    )
    assert json.loads(meta_path.read_text(encoding="utf-8")) == {
        "payload": "data.parquet",
        "size_bytes": len(payload),
    }
    assert meta_path.with_name("data.parquet").read_bytes() == payload


def test_apply_writes_nothing_when_v1_raw_identity_is_blocked(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "current-storage"
    storage_root.mkdir()
    valid_meta_path, _ = _write_v1_raw_object(
        storage_root=storage_root,
        source_name="daily_bar",
        partition="trade_date=2026-07-15",
    )
    blocked_meta_path, _ = _write_v1_raw_object(
        storage_root=storage_root,
        source_name="daily_basic",
        partition="trade_date=2026-07-15",
        legacy_raw_identity=(
            "raw/tushare/daily_basic/trade_date=2026-07-14/data.parquet"
        ),
    )
    original_valid_meta = valid_meta_path.read_bytes()
    original_blocked_meta = blocked_meta_path.read_bytes()

    completed = _run_migration(
        storage_root=storage_root,
        should_apply=True,
    )

    assert completed.returncode == 1
    assert "legacy output.path does not match the current raw identity" in (
        completed.stdout
    )
    assert (
        "SUMMARY mode=apply current=0 migratable=1 migrated=0 blocked=1"
        in completed.stdout
    )
    assert valid_meta_path.read_bytes() == original_valid_meta
    assert blocked_meta_path.read_bytes() == original_blocked_meta
