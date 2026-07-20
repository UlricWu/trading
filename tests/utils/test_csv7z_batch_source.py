# filepath: tests/utils/test_csv7z_batch_source.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Never

import pyarrow as pa
import pytest

import src.utils.csv7z_batch_source as csv7z_module
from src.utils.csv7z_batch_source import Csv7zBatchSource


class _FailingCloseStdout:
    def readline(self, _size: int = -1) -> bytes:
        return b"symbol\n"

    def close(self) -> None:
        raise OSError("stdout close failed")


class _CleanupProbeProcess:
    def __init__(self) -> None:
        self.stdout = _FailingCloseStdout()
        self.stderr = None
        self.terminate_called = False

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminate_called = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        raise AssertionError("kill must not be needed when terminate succeeds")


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


def test_csv7z_batch_source_applies_the_formal_null_token_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    payload = (
        b"empty,space,upper,not_available,lower,other\n"
        b'"", ,NULL,N/A,nan,Null\n'
    )
    monkeypatch.setattr(
        csv7z_module,
        "open_extract_stdout",
        lambda _path: _python_payload_process(payload),
    )

    table = pa.Table.from_batches(list(Csv7zBatchSource(archive_path)))

    assert table.to_pydict() == {
        "empty": [None],
        "space": [None],
        "upper": [None],
        "not_available": [None],
        "lower": [None],
        "other": ["Null"],
    }


def test_failed_open_still_terminates_process_when_stdout_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    process = _CleanupProbeProcess()

    def fail_open_csv(
        _input_file: object,
        *,
        read_options: object,
        convert_options: object,
    ) -> Never:
        del read_options, convert_options
        raise ValueError("CSV parse initialization failed")

    monkeypatch.setattr(
        csv7z_module,
        "open_extract_stdout",
        lambda _path: process,
    )
    monkeypatch.setattr(csv7z_module.arrow_csv, "open_csv", fail_open_csv)

    with pytest.raises(ValueError, match="parse initialization failed") as error_info:
        list(Csv7zBatchSource(archive_path))

    assert process.terminate_called
    assert any(
        "stdout close failed" in note
        for note in getattr(error_info.value, "__notes__", ())
    )


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
