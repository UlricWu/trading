# filepath: tests/access/test_meta.py
"""Object-side Meta contract tests."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from src.access import meta
from src.utils.path import PathManager


def _write_payload(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_missing_meta_is_the_only_optional_find_result(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    meta_path = tmp_path / "raw" / "source" / "meta.json"
    expected_payload = meta_path.parent / "data.bin"

    assert (
        meta.find(
            pm=pm,
            meta_path=meta_path,
            expected_payload_path=expected_payload,
        )
        is None
    )
    with pytest.raises(FileNotFoundError, match="required Meta"):
        meta.require(
            pm=pm,
            meta_path=meta_path,
            expected_payload_path=expected_payload,
        )


def test_payload_identity_is_size_only(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    payload_path = _write_payload(
        tmp_path / "raw" / "source" / "payload.bin",
        b"aa",
    )
    meta_path = payload_path.parent / "meta.json"
    meta.commit(pm=pm, payload_path=payload_path)

    assert json.loads(meta_path.read_text(encoding="utf-8")) == {
        "payload": "payload.bin",
        "size_bytes": 2,
    }

    payload_path.write_bytes(b"bb")
    loaded = meta.require(pm=pm, meta_path=meta_path)
    assert loaded.payload_path == payload_path
    assert loaded.size_bytes == 2

    payload_path.write_bytes(b"ccc")
    with pytest.raises(RuntimeError, match="payload size changed"):
        meta.require(pm=pm, meta_path=meta_path)


def test_only_direct_upstream_is_validated(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    raw_payload = _write_payload(tmp_path / "raw" / "data.bin", b"a")
    raw_meta = raw_payload.parent / "meta.json"
    meta.commit(pm=pm, payload_path=raw_payload)

    processed_payload = _write_payload(
        tmp_path / "processed" / "data.parquet",
        b"processed",
    )
    processed_meta = processed_payload.parent / "meta.json"
    meta.commit(
        pm=pm,
        payload_path=processed_payload,
        upstream_meta_path=raw_meta,
    )

    derived_payload = _write_payload(
        tmp_path / "features" / "data.parquet",
        b"derived",
    )
    derived_meta = derived_payload.parent / "meta.json"
    meta.commit(
        pm=pm,
        payload_path=derived_payload,
        upstream_meta_path=processed_meta,
    )
    assert json.loads(derived_meta.read_text(encoding="utf-8"))["upstream"] == {
        "meta_path": "processed/meta.json",
        "size_bytes": len(b"processed"),
    }

    raw_payload.write_bytes(b"changed")
    loaded = meta.require(pm=pm, meta_path=derived_meta)
    assert isinstance(loaded, meta.MetaRecord)
    assert loaded.payload_path == derived_payload
    assert loaded.upstream == (
        PurePosixPath("processed/meta.json"),
        len(b"processed"),
    )
    with pytest.raises(RuntimeError, match="payload size changed"):
        meta.require(pm=pm, meta_path=processed_meta)


def test_existing_malformed_meta_raises(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    meta_path = tmp_path / "raw" / "meta.json"
    meta_path.write_text("{", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid Meta JSON"):
        meta.find(pm=pm, meta_path=meta_path)


def test_symbol_slices_are_nonempty_contiguous_ranges(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    payload_path = _write_payload(
        tmp_path / "processed" / "data.parquet",
        b"payload",
    )
    meta_path = payload_path.parent / "meta.json"
    expected = {"000001": range(0, 2), "600000": range(2, 3)}
    meta.commit(
        pm=pm,
        payload_path=payload_path,
        symbol_slices=expected,
    )

    loaded = meta.require(
        pm=pm,
        meta_path=meta_path,
        expected_payload_path=payload_path,
    )
    assert loaded.symbol_slices == expected

    with pytest.raises(RuntimeError, match="contiguous from zero"):
        meta.commit(
            pm=pm,
            payload_path=payload_path,
            symbol_slices={"000001": range(1, 2)},
        )


def test_remote_level2_lineage_survives_removing_all_local_raw(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    raw = _write_payload(pm.raw_payload(
        broker="level2_ftp", source_name="sz_trade", trade_date="2026-09-21",
        payload_file="SZ_Trade.csv.7z",
    ), b"raw")
    raw_meta = raw.parent / "meta.json"
    meta.commit(pm=pm, payload_path=raw)
    paths = pm.processed_object(dataset_name="sz_trade", version="v1", trade_date="2026-09-21")
    _write_payload(paths.payload_path, b"processed")
    meta.commit(pm=pm, payload_path=paths.payload_path, upstream_meta_path=raw_meta,
                symbol_slices={"000001": range(1)})
    processed_meta = paths.meta_path.read_bytes()
    archive = meta.RemoteRawRecord(raw.name, 3, f"/level2/2026-09-21/{raw.name}")
    meta.commit_remote_raw(pm=pm, meta_path=raw_meta, record=archive)
    raw.unlink()
    assert meta.find_level2_raw(pm=pm, meta_path=raw_meta) == archive
    assert meta.require(pm=pm, meta_path=paths.meta_path).payload_path == paths.payload_path
    assert paths.meta_path.read_bytes() == processed_meta
    with pytest.raises(RuntimeError, match="no required local payload"):
        meta.require(pm=pm, meta_path=raw_meta)
    changed = meta.RemoteRawRecord(raw.name, 4, archive.remote_path)
    meta.commit_remote_raw(pm=pm, meta_path=raw_meta, record=changed)
    with pytest.raises(RuntimeError, match="upstream payload size changed"):
        meta.require(pm=pm, meta_path=paths.meta_path)


@pytest.mark.parametrize("broker", ["tushare", "other"])
def test_cloud_schema_cannot_relax_other_raw_payload_checks(tmp_path: Path, broker: str) -> None:
    pm = PathManager(tmp_path)
    raw_meta = pm.raw_meta(broker=broker, source_name="daily_bar", trade_date="2026-09-21")
    archive = meta.RemoteRawRecord("SZ_Trade.csv.7z", 3, "/cloud/SZ_Trade.csv.7z")
    with pytest.raises(RuntimeError, match="Level-2 daily raw"):
        meta.commit_remote_raw(pm=pm, meta_path=raw_meta, record=archive)
    _write_payload(raw_meta, json.dumps({"payload": archive.payload, "size_bytes": 3, "remote_path": archive.remote_path}).encode())
    with pytest.raises(RuntimeError, match="Level-2 daily raw"):
        meta.require(pm=pm, meta_path=raw_meta)


@pytest.mark.parametrize("remote_path,size", [
    ("relative/SZ_Trade.csv.7z", 3), ("/cloud/../SZ_Trade.csv.7z", 3),
    ("/cloud/different.csv.7z", 3), ("/cloud/SZ_Trade.csv.7z", 0),
    ("/cloud/SZ_Trade.csv.7z", True),
])
def test_invalid_remote_raw_record_is_never_published(tmp_path: Path, remote_path: str, size: int) -> None:
    pm = PathManager(tmp_path)
    raw_meta = pm.raw_meta(broker="level2_ftp", source_name="sz_trade", trade_date="2026-09-21")
    with pytest.raises(RuntimeError):
        meta.commit_remote_raw(pm=pm, meta_path=raw_meta,
                               record=meta.RemoteRawRecord("SZ_Trade.csv.7z", size, remote_path))
    assert not raw_meta.exists()
