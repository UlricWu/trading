# filepath: tests/utils/test_csv7z_batch_source.py

from __future__ import annotations

import io
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pyarrow as pa
import pytest

import src.utils.csv7z_batch_source as csv7z_module
from src.utils.csv7z_batch_source import open_csv7z_batches


class _FailingCloseStream(io.BufferedReader):
    def close(self) -> None:
        if self.closed:
            return
        super().close()
        raise OSError("stdout close failed")


class _ProbeProcess:
    def __init__(
        self,
        stdout: io.BufferedReader,
        *,
        return_code: int = 0,
    ) -> None:
        self.stdout = stdout
        self.return_code = return_code
        self.is_running = True
        self.kill_called = False
        self.wait_called = False

    def poll(self) -> int | None:
        return None if self.is_running else self.return_code

    def kill(self) -> None:
        self.kill_called = True
        self.is_running = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_called = True
        self.is_running = False
        return self.return_code


def _install_payload_process(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: bytes,
    return_code: int = 0,
) -> tuple[_ProbeProcess, list[str], list[str]]:
    candidates_checked: list[str] = []
    recorded_args: list[str] = []
    extraction_process = _ProbeProcess(
        io.BufferedReader(io.BytesIO(payload)),
        return_code=return_code,
    )

    def which(candidate: str) -> str | None:
        candidates_checked.append(candidate)
        return "/usr/bin/7za" if candidate == "7za" else None

    def popen(
        args: Sequence[str],
        *,
        stdin: int,
        stdout: int,
    ) -> subprocess.Popen[bytes]:
        assert stdin == subprocess.DEVNULL
        assert stdout == subprocess.PIPE
        recorded_args.extend(args)
        return cast("subprocess.Popen[bytes]", extraction_process)

    monkeypatch.setattr(csv7z_module.shutil, "which", which)
    monkeypatch.setattr(csv7z_module.subprocess, "Popen", popen)
    return extraction_process, candidates_checked, recorded_args


def test_open_csv7z_batches_uses_cli_order_and_exact_member(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload[1].csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _, candidates_checked, recorded_args = _install_payload_process(
        monkeypatch,
        payload=b"symbol\n000001\n",
    )

    with open_csv7z_batches(archive_path) as batches:
        assert len(list(batches)) == 1

    assert candidates_checked == ["7zz", "7za"]
    assert recorded_args == [
        "/usr/bin/7za",
        "x",
        "-so",
        "-spd",
        "-bd",
        "-bb0",
        "-bso0",
        "-bsp0",
        "-bse2",
        "--",
        str(archive_path),
        "payload[1].csv",
    ]


def test_open_csv7z_batches_streams_all_columns_as_strings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(
        monkeypatch,
        payload=b'"sym,bol",value\r\n000001,1.25\r\n',
    )

    with open_csv7z_batches(archive_path) as batches:
        table = pa.Table.from_batches(list(batches))

    assert table.column_names == ["sym,bol", "value"]
    assert table.to_pydict() == {"sym,bol": ["000001"], "value": ["1.25"]}


def test_open_csv7z_batches_applies_the_formal_null_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(
        monkeypatch,
        payload=(
            b"empty,space,upper,not_available,lower,other\n"
            b'"", ,NULL,N/A,nan,Null\n'
        ),
    )

    with open_csv7z_batches(archive_path) as batches:
        table = pa.Table.from_batches(list(batches))

    assert table.to_pydict() == {
        "empty": [None],
        "space": [None],
        "upper": [None],
        "not_available": [None],
        "lower": [None],
        "other": ["Null"],
    }


def test_open_csv7z_batches_accepts_header_only_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(monkeypatch, payload=b"symbol,value\n")

    with open_csv7z_batches(archive_path) as batches:
        assert list(batches) == []


@pytest.mark.parametrize(
    "payload",
    [
        b"first,\xef\xbb\xbfsecond\n1,2\n",
        b"first,second\n\xef\xbb\xbf1,2\n",
        b"first,second\n1,\xef\xbb\xbf2\n",
    ],
)
def test_open_csv7z_batches_rejects_bom_outside_file_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: bytes,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(monkeypatch, payload=payload)

    with pytest.raises(ValueError, match="BOM is only allowed at the start"):
        with open_csv7z_batches(archive_path) as batches:
            list(batches)


def test_open_csv7z_batches_rejects_missing_member_as_missing_header(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(monkeypatch, payload=b"")

    with pytest.raises(ValueError, match="must contain a header row"):
        with open_csv7z_batches(archive_path):
            pass


def test_open_csv7z_batches_rejects_invalid_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(monkeypatch, payload=b"symbol,value\n000001\n")

    with pytest.raises(ValueError, match="CSV body is structurally invalid"):
        with open_csv7z_batches(archive_path) as batches:
            list(batches)


def test_open_csv7z_batches_accepts_doubled_quotes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(monkeypatch, payload=b'value\n"x""y"\n')

    with open_csv7z_batches(archive_path) as batches:
        table = pa.Table.from_batches(list(batches))

    assert table.to_pydict() == {"value": ['x"y']}


def test_open_csv7z_batches_rejects_bom_across_reads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    arrow_block_size = 1024 * 1024
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(
        monkeypatch,
        payload=(
            b"value\n"
            + (b"x" * (arrow_block_size - 1))
            + b"\xef\xbb\xbf\n"
        ),
    )

    with pytest.raises(ValueError, match="BOM is only allowed at the start"):
        with open_csv7z_batches(archive_path) as batches:
            list(batches)


def test_open_csv7z_batches_uses_pyarrow_permissive_quoting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(monkeypatch, payload=b'a,b\n1,"x')

    with open_csv7z_batches(archive_path) as batches:
        table = pa.Table.from_batches(list(batches))

    assert table.to_pydict() == {"a": ["1"], "b": ["x"]}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"value\n\n", {"value": [None]}),
        (
            b"first,second\n1,2\n\n3,4\n",
            {
                "first": ["1", None, "3"],
                "second": ["2", None, "4"],
            },
        ),
    ],
)
def test_open_csv7z_batches_passes_empty_physical_lines_to_pyarrow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: bytes,
    expected: dict[str, list[str | None]],
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(monkeypatch, payload=payload)

    with open_csv7z_batches(archive_path) as batches:
        table = pa.Table.from_batches(list(batches))

    assert table.to_pydict() == expected


def test_open_csv7z_batches_uses_pyarrow_newline_parsing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(
        monkeypatch,
        payload=b'symbol,value\n000001,"line one\nline two"\n',
    )

    with open_csv7z_batches(archive_path) as batches:
        table = pa.Table.from_batches(list(batches))

    assert table.to_pydict() == {
        "symbol": ["000001"],
        "value": ["line one\nline two"],
    }


def test_open_csv7z_batches_rejects_nonzero_extractor_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    _install_payload_process(
        monkeypatch,
        payload=b"symbol,value\n000001,1\n",
        return_code=2,
    )

    with pytest.raises(RuntimeError, match="return_code=2"):
        with open_csv7z_batches(archive_path) as batches:
            list(batches)


def test_open_csv7z_batches_kills_an_unconsumed_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    process, _, _ = _install_payload_process(
        monkeypatch,
        payload=b"symbol,value\n000001,1\n",
    )

    with open_csv7z_batches(archive_path) as batches:
        next(batches)

    assert process.kill_called
    assert process.wait_called


def test_open_csv7z_batches_cleans_up_for_base_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    process, _, _ = _install_payload_process(
        monkeypatch,
        payload=b"symbol,value\n000001,1\n",
    )

    with pytest.raises(KeyboardInterrupt):
        with open_csv7z_batches(archive_path) as batches:
            next(batches)
            raise KeyboardInterrupt

    assert process.kill_called
    assert process.wait_called


def test_open_csv7z_batches_preserves_consumer_error_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    process = _ProbeProcess(
        _FailingCloseStream(io.BytesIO(b"symbol,value\n000001,1\n"))
    )

    def popen(
        _args: Sequence[str],
        *,
        stdin: int,
        stdout: int,
    ) -> subprocess.Popen[bytes]:
        assert stdin == subprocess.DEVNULL
        assert stdout == subprocess.PIPE
        return cast("subprocess.Popen[bytes]", process)

    monkeypatch.setattr(
        csv7z_module.shutil,
        "which",
        lambda _candidate: "/usr/bin/7zz",
    )
    monkeypatch.setattr(csv7z_module.subprocess, "Popen", popen)

    with pytest.raises(LookupError, match="consumer failed") as error_info:
        with open_csv7z_batches(archive_path) as batches:
            next(batches)
            raise LookupError("consumer failed")

    assert any(
        "stdout close failed" in note
        for note in getattr(error_info.value, "__notes__", ())
    )


def test_open_csv7z_batches_owns_cleanup_error_without_primary_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.write_bytes(b"archive-placeholder")
    process = _ProbeProcess(
        _FailingCloseStream(io.BytesIO(b"symbol,value\n000001,1\n"))
    )

    def popen(
        _args: Sequence[str],
        *,
        stdin: int,
        stdout: int,
    ) -> subprocess.Popen[bytes]:
        assert stdin == subprocess.DEVNULL
        assert stdout == subprocess.PIPE
        return cast("subprocess.Popen[bytes]", process)

    monkeypatch.setattr(
        csv7z_module.shutil,
        "which",
        lambda _candidate: "/usr/bin/7zz",
    )
    monkeypatch.setattr(csv7z_module.subprocess, "Popen", popen)

    with pytest.raises(RuntimeError) as error_info:
        with open_csv7z_batches(archive_path) as batches:
            next(batches)

    assert isinstance(error_info.value.__cause__, OSError)
    assert any(
        "stdout close failed" in note
        for note in getattr(error_info.value, "__notes__", ())
    )


def test_open_csv7z_batches_requires_source_suffix(tmp_path: Path) -> None:
    archive_path = tmp_path / "payload.7z"
    archive_path.write_bytes(b"archive-placeholder")

    with pytest.raises(ValueError, match=r"\.csv\.7z"):
        with open_csv7z_batches(archive_path):
            pass


def test_open_csv7z_batches_accepts_symlink_to_regular_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "stored.7z"
    target_path.write_bytes(b"archive-placeholder")
    archive_path = tmp_path / "payload.csv.7z"
    archive_path.symlink_to(target_path)
    _install_payload_process(monkeypatch, payload=b"symbol\n000001\n")

    with open_csv7z_batches(archive_path) as batches:
        table = pa.Table.from_batches(list(batches))

    assert table.to_pydict() == {"symbol": ["000001"]}


def test_open_csv7z_batches_rejects_non_file_paths(tmp_path: Path) -> None:
    directory_path = tmp_path / "directory.csv.7z"
    directory_path.mkdir()
    broken_symlink_path = tmp_path / "broken.csv.7z"
    broken_symlink_path.symlink_to(tmp_path / "missing.7z")

    for archive_path in (directory_path, broken_symlink_path):
        with pytest.raises(FileNotFoundError):
            with open_csv7z_batches(archive_path):
                pass


def test_open_csv7z_batches_requires_path_type() -> None:
    archive_path = cast(Path, "payload.csv.7z")

    with pytest.raises(TypeError):
        with open_csv7z_batches(archive_path):
            pass


@pytest.mark.contract
def test_open_csv7z_batches_extracts_only_the_named_member(tmp_path: Path) -> None:
    expected_csv = tmp_path / "payload.csv"
    extra_csv = tmp_path / "extra.csv"
    archive_path = tmp_path / "payload.csv.7z"
    expected_csv.write_text("symbol\n000001\n", encoding="utf-8")
    extra_csv.write_text("symbol\n999999\n", encoding="utf-8")
    subprocess.run(
        ["7zz", "a", str(archive_path), str(expected_csv), str(extra_csv)],
        check=True,
        stdout=subprocess.DEVNULL,
    )

    with open_csv7z_batches(archive_path) as batches:
        table = pa.Table.from_batches(list(batches))

    assert table.to_pydict() == {"symbol": ["000001"]}
