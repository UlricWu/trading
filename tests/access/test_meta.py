# filepath: tests/access/test_meta.py
"""Object-side Meta contract tests."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from src.access import meta


def _write_payload(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_missing_meta_is_the_only_unavailable_result(tmp_path: Path) -> None:
    meta_path = tmp_path / "raw" / "source" / "meta.json"
    expected_payload = meta_path.parent / "data.bin"

    assert (
        meta.load(
            meta_path=meta_path,
            expected_payload_path=expected_payload,
            storage_root=tmp_path,
        )
        is None
    )


def test_payload_identity_is_size_only(tmp_path: Path) -> None:
    payload_path = _write_payload(
        tmp_path / "raw" / "source" / "payload.bin",
        b"aa",
    )
    meta_path = payload_path.parent / "meta.json"
    meta.write(payload_path=payload_path, storage_root=tmp_path)

    assert json.loads(meta_path.read_text(encoding="utf-8")) == {
        "payload": "payload.bin",
        "size_bytes": 2,
    }

    payload_path.write_bytes(b"bb")
    loaded = meta.load(
        meta_path=meta_path,
        storage_root=tmp_path,
    )
    assert loaded is not None
    assert loaded.payload_path == payload_path
    assert loaded.size_bytes == 2

    payload_path.write_bytes(b"ccc")
    with pytest.raises(RuntimeError, match="payload size changed"):
        meta.load(meta_path=meta_path, storage_root=tmp_path)


def test_only_direct_upstream_is_validated(tmp_path: Path) -> None:
    raw_payload = _write_payload(tmp_path / "raw" / "data.bin", b"a")
    raw_meta = raw_payload.parent / "meta.json"
    meta.write(payload_path=raw_payload, storage_root=tmp_path)

    processed_payload = _write_payload(
        tmp_path / "processed" / "data.parquet",
        b"processed",
    )
    processed_meta = processed_payload.parent / "meta.json"
    meta.write(
        payload_path=processed_payload,
        storage_root=tmp_path,
        upstream_meta_path=raw_meta,
    )

    derived_payload = _write_payload(
        tmp_path / "derived" / "data.parquet",
        b"derived",
    )
    derived_meta = derived_payload.parent / "meta.json"
    meta.write(
        payload_path=derived_payload,
        storage_root=tmp_path,
        upstream_meta_path=processed_meta,
    )
    assert json.loads(derived_meta.read_text(encoding="utf-8"))["upstream"] == {
        "meta_path": "processed/meta.json",
        "size_bytes": len(b"processed"),
    }

    raw_payload.write_bytes(b"changed")
    loaded = meta.load(
        meta_path=derived_meta,
        storage_root=tmp_path,
    )
    assert loaded is not None
    assert isinstance(loaded, meta.MetaRecord)
    assert loaded.payload_path == derived_payload
    assert loaded.upstream == (
        PurePosixPath("processed/meta.json"),
        len(b"processed"),
    )
    with pytest.raises(RuntimeError, match="payload size changed"):
        meta.load(meta_path=processed_meta, storage_root=tmp_path)


def test_existing_malformed_meta_raises(tmp_path: Path) -> None:
    meta_path = tmp_path / "raw" / "meta.json"
    meta_path.parent.mkdir(parents=True)
    meta_path.write_text("{", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid Meta JSON"):
        meta.load(meta_path=meta_path, storage_root=tmp_path)


def test_symbol_slices_are_nonempty_contiguous_ranges(tmp_path: Path) -> None:
    payload_path = _write_payload(
        tmp_path / "processed" / "data.parquet",
        b"payload",
    )
    meta_path = payload_path.parent / "meta.json"
    expected = {"000001": range(0, 2), "600000": range(2, 3)}
    meta.write(
        payload_path=payload_path,
        storage_root=tmp_path,
        symbol_slices=expected,
    )

    loaded = meta.load(
        meta_path=meta_path,
        expected_payload_path=payload_path,
        storage_root=tmp_path,
    )
    assert loaded is not None
    assert loaded.symbol_slices == expected

    with pytest.raises(RuntimeError, match="contiguous from zero"):
        meta.write(
            payload_path=payload_path,
            storage_root=tmp_path,
            symbol_slices={"000001": range(1, 2)},
        )
