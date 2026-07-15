# filepath: tests/utils/test_csv7z_batch_source.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pytest

import src.utils.csv7z_batch_source as csv7z_module
from src.utils.csv7z_batch_source import Csv7zBatchSource


def _python_payload_process(
    payload: bytes, *, return_code: int = 0
) -> subprocess.Popen[bytes]:
    script = (
        "import sys; "
        f"sys.stdout.buffer.write({payload!r}); "
        "sys.stdout.buffer.flush(); "
        f"sys.stderr.write('extract failed' if {return_code} else ''); "
        f"raise SystemExit({return_code})"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_csv7z_batch_source_streams_all_columns_as_strings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    payload = b'"sym,bol",value\n000001,1.25\n'
    monkeypatch.setattr(
        csv7z_module,
        "open_extract_stdout",
        lambda _path: _python_payload_process(payload),
    )

    table = pa.Table.from_batches(list(Csv7zBatchSource(archive_path)))

    assert table.column_names == ["sym,bol", "value"]
    assert table.to_pydict() == {"sym,bol": ["000001"], "value": ["1.25"]}


def test_csv7z_batch_source_translates_nonzero_extractor_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    monkeypatch.setattr(
        csv7z_module,
        "open_extract_stdout",
        lambda _path: _python_payload_process(
            b"symbol,value\n000001,1\n", return_code=2
        ),
    )

    with pytest.raises(RuntimeError, match="return_code=2"):
        list(Csv7zBatchSource(archive_path))


def test_csv7z_batch_source_translates_failure_before_header(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    monkeypatch.setattr(
        csv7z_module,
        "open_extract_stdout",
        lambda _path: _python_payload_process(b"", return_code=3),
    )

    with pytest.raises(RuntimeError, match="before the CSV header.*return_code=3"):
        list(Csv7zBatchSource(archive_path))


def test_csv7z_batch_source_requires_exact_source_suffix(tmp_path: Path) -> None:
    archive_path = tmp_path / "payload.7z"
    archive_path.write_bytes(b"archive-placeholder")

    with pytest.raises(ValueError, match=r"\.csv\.7z"):
        Csv7zBatchSource(archive_path)
