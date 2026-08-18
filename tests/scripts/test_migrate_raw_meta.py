# filepath: tests/scripts/test_migrate_raw_meta.py
"""Raw Meta migration script regression tests."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from src.access import meta
from src.utils.path import PathManager


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_legacy_raw_object(
    *,
    payload_path: Path,
    payload: bytes = b"raw-payload",
) -> Path:
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_bytes(payload)
    meta_path = payload_path.parent / "meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "legacy_writer": "pre-migration",
                "payload": payload_path.name,
                "size_bytes": len(payload),
            }
        ),
        encoding="utf-8",
    )
    return meta_path


def _run_migration(
    *,
    storage_root: Path,
    additional_arguments: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    arguments = [
        sys.executable,
        "-m",
        "scripts.migrate_raw_meta",
        "--storage-root",
        str(storage_root),
        *additional_arguments,
    ]
    return subprocess.run(
        arguments,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_help_documents_dry_run_and_apply_examples() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.migrate_raw_meta", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Dry-run preflight:" in completed.stdout
    assert (
        "python -m scripts.migrate_raw_meta "
        "--storage-root /absolute/storage/root"
        in completed.stdout
    )
    assert (
        "python -m scripts.migrate_raw_meta "
        "--storage-root /absolute/storage/root --apply"
        in completed.stdout
    )


def test_dry_run_reports_migratable_meta_without_writing(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    payload_path = pm.raw_payload(
        broker="tushare",
        source_name="daily_bar",
        trade_date="2026-07-15",
        payload_file="data.parquet",
    )
    meta_path = _write_legacy_raw_object(payload_path=payload_path)
    original_meta = meta_path.read_bytes()

    completed = _run_migration(storage_root=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert meta_path.read_bytes() == original_meta
    assert "MIGRATABLE meta_path=raw/tushare/daily_bar/" in completed.stdout
    assert (
        "SUMMARY mode=dry-run current=0 migratable=1 migrated=0 blocked=0"
        in completed.stdout
    )


def test_apply_migrates_daily_and_annual_meta_then_is_idempotent(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    daily_payload = pm.raw_payload(
        broker="tushare",
        source_name="daily_bar",
        trade_date="2026-07-15",
        payload_file="data.parquet",
    )
    annual_payload = pm.raw_year_payload(
        broker="tushare",
        source_name="trade_calendar",
        calendar_year=2026,
        payload_file="data.parquet",
    )
    daily_meta = _write_legacy_raw_object(payload_path=daily_payload)
    annual_meta = _write_legacy_raw_object(
        payload_path=annual_payload,
        payload=b"calendar",
    )

    first_run = _run_migration(
        storage_root=tmp_path,
        additional_arguments=("--apply",),
    )

    assert first_run.returncode == 0, first_run.stderr
    assert (
        "SUMMARY mode=apply current=0 migratable=2 migrated=2 blocked=0"
        in first_run.stdout
    )
    assert meta.require(
        pm=pm,
        meta_path=daily_meta,
        expected_payload_path=daily_payload,
    ).size_bytes == len(b"raw-payload")
    assert meta.require(
        pm=pm,
        meta_path=annual_meta,
        expected_payload_path=annual_payload,
    ).size_bytes == len(b"calendar")

    second_run = _run_migration(
        storage_root=tmp_path,
        additional_arguments=("--apply",),
    )

    assert second_run.returncode == 0, second_run.stderr
    assert (
        "SUMMARY mode=apply current=2 migratable=0 migrated=0 blocked=0"
        in second_run.stdout
    )


def test_apply_preflight_blocks_all_writes_when_payload_identity_changed(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    eligible_payload = pm.raw_payload(
        broker="tushare",
        source_name="daily_bar",
        trade_date="2026-07-15",
        payload_file="data.parquet",
    )
    eligible_meta = _write_legacy_raw_object(payload_path=eligible_payload)
    eligible_before = eligible_meta.read_bytes()

    changed_payload = pm.raw_payload(
        broker="tushare",
        source_name="daily_basic",
        trade_date="2026-07-15",
        payload_file="data.parquet",
    )
    changed_meta = _write_legacy_raw_object(payload_path=changed_payload)
    changed_payload.write_bytes(b"changed-size")

    completed = _run_migration(
        storage_root=tmp_path,
        additional_arguments=("--apply",),
    )

    assert completed.returncode == 1
    assert eligible_meta.read_bytes() == eligible_before
    assert "legacy payload size changed" in completed.stdout
    assert (
        "SUMMARY mode=apply current=0 migratable=1 migrated=0 blocked=1"
        in completed.stdout
    )
    assert changed_meta.exists()


def test_apply_blocks_ambiguous_raw_partition(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    payload_path = pm.raw_payload(
        broker="level2_ftp",
        source_name="sz_trade",
        trade_date="2026-07-15",
        payload_file="SZ_Trade.csv.7z",
    )
    meta_path = _write_legacy_raw_object(payload_path=payload_path)
    (payload_path.parent / "unexpected.bin").write_bytes(b"other")
    original_meta = meta_path.read_bytes()

    completed = _run_migration(
        storage_root=tmp_path,
        additional_arguments=("--apply",),
    )

    assert completed.returncode == 1
    assert meta_path.read_bytes() == original_meta
    assert "raw partition has unexpected siblings" in completed.stdout
    assert (
        "SUMMARY mode=apply current=0 migratable=0 migrated=0 blocked=1"
        in completed.stdout
    )


def test_missing_meta_does_not_publish_an_orphan_payload(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    payload_path = pm.raw_payload(
        broker="tushare",
        source_name="daily_bar",
        trade_date="2026-07-15",
        payload_file="data.parquet",
    )
    payload_path.parent.mkdir(parents=True)
    payload_path.write_bytes(b"orphan")

    completed = _run_migration(
        storage_root=tmp_path,
        additional_arguments=("--apply",),
    )

    assert completed.returncode == 0, completed.stderr
    assert not (payload_path.parent / "meta.json").exists()
    assert (
        "SUMMARY mode=apply current=0 migratable=0 migrated=0 blocked=0"
        in completed.stdout
    )


@pytest.mark.parametrize(
    ("legacy_json", "error_fragment"),
    [
        ("{", "legacy Meta JSON is invalid"),
        (
            '{"payload":"data.parquet","payload":"data.parquet",'
            '"size_bytes":3}',
            "duplicate JSON key",
        ),
    ],
)
def test_apply_blocks_invalid_legacy_json(
    legacy_json: str,
    error_fragment: str,
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    payload_path = pm.raw_payload(
        broker="tushare",
        source_name="daily_bar",
        trade_date="2026-07-15",
        payload_file="data.parquet",
    )
    payload_path.parent.mkdir(parents=True)
    payload_path.write_bytes(b"raw")
    meta_path = payload_path.parent / "meta.json"
    meta_path.write_text(legacy_json, encoding="utf-8")

    completed = _run_migration(
        storage_root=tmp_path,
        additional_arguments=("--apply",),
    )

    assert completed.returncode == 1
    assert meta_path.read_text(encoding="utf-8") == legacy_json
    assert error_fragment in completed.stdout
    assert (
        "SUMMARY mode=apply current=0 migratable=0 migrated=0 blocked=1"
        in completed.stdout
    )


def test_apply_blocks_meta_outside_a_formal_raw_partition(tmp_path: Path) -> None:
    PathManager(tmp_path)
    payload_path = (
        tmp_path
        / "raw"
        / "tushare"
        / "daily_bar"
        / "date=2026-07-15"
        / "data.parquet"
    )
    meta_path = _write_legacy_raw_object(payload_path=payload_path)
    original_meta = meta_path.read_bytes()

    completed = _run_migration(
        storage_root=tmp_path,
        additional_arguments=("--apply",),
    )

    assert completed.returncode == 1
    assert meta_path.read_bytes() == original_meta
    assert "raw partition must use trade_date=YYYY-MM-DD" in completed.stdout
    assert (
        "SUMMARY mode=apply current=0 migratable=0 migrated=0 blocked=1"
        in completed.stdout
    )


def test_apply_blocks_a_symlinked_raw_partition(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    target_payload = (
        tmp_path
        / "symlink-target"
        / "trade_date=2026-07-15"
        / "data.parquet"
    )
    target_meta = _write_legacy_raw_object(payload_path=target_payload)
    linked_partition = (
        pm.storage_root
        / "raw"
        / "tushare"
        / "daily_bar"
        / "trade_date=2026-07-15"
    )
    linked_partition.parent.mkdir(parents=True)
    linked_partition.symlink_to(target_payload.parent, target_is_directory=True)
    original_meta = target_meta.read_bytes()

    completed = _run_migration(
        storage_root=tmp_path,
        additional_arguments=("--apply",),
    )

    assert completed.returncode == 1
    assert target_meta.read_bytes() == original_meta
    assert "raw scan encountered a symlink" in completed.stdout
    assert (
        "SUMMARY mode=apply current=0 migratable=0 migrated=0 blocked=1"
        in completed.stdout
    )
