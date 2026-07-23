# filepath: src/utils/csv7z_batch_source.py
"""Open scoped Arrow batch streams from source-native ``.csv.7z`` files."""

from __future__ import annotations

import csv as standard_csv
import io
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, cast

import pyarrow as pa
import pyarrow.csv as arrow_csv

_MAX_HEADER_BYTES: Final = 1024 * 1024
_PROCESS_REAP_TIMEOUT_SECONDS: Final = 5.0
_NULL_VALUES: Final[tuple[str, ...]] = ("", " ", "NULL", "N/A", "nan")
_UTF8_BOM: Final = b"\xef\xbb\xbf"


class _Utf8BomRejectingStream(io.RawIOBase):
    """Reject UTF-8 BOM bytes after the already-consumed CSV header."""

    def __init__(
        self,
        source: io.BufferedReader,
        *,
        archive_path: Path,
    ) -> None:
        super().__init__()
        self._source = source
        self._archive_path = archive_path
        self._bom_tail = b""

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        payload = self._source.read(len(buffer))
        if not payload:
            return 0

        bom_window = self._bom_tail + payload
        if _UTF8_BOM in bom_window:
            raise ValueError(
                "UTF-8 BOM is only allowed at the start of the CSV; "
                f"archive_path={self._archive_path}"
            )
        self._bom_tail = bom_window[-2:]

        buffer[: len(payload)] = payload
        return len(payload)


def _parse_csv_header(
    header_bytes: bytes,
    *,
    archive_path: Path,
) -> list[str]:
    """Return validated column names from the first physical CSV line."""
    if len(header_bytes) > _MAX_HEADER_BYTES:
        raise ValueError(
            "CSV header exceeds 1048576 bytes; "
            f"archive_path={archive_path}"
        )

    try:
        header_text = header_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"CSV header must use UTF-8; archive_path={archive_path}"
        ) from exc
    if "\ufeff" in header_text:
        raise ValueError(
            "UTF-8 BOM is only allowed at the start of the CSV; "
            f"archive_path={archive_path}"
        )

    try:
        header_rows = list(
            standard_csv.reader(
                io.StringIO(header_text, newline=""),
                strict=True,
            )
        )
    except standard_csv.Error as exc:
        raise ValueError(
            f"CSV header is structurally invalid; archive_path={archive_path}"
        ) from exc
    if len(header_rows) != 1 or not header_rows[0]:
        raise ValueError(
            "CSV payload must contain exactly one non-empty header row; "
            f"archive_path={archive_path}"
        )

    column_names = header_rows[0]
    if any(
        not column_name
        or column_name.strip() != column_name
        or "\r" in column_name
        or "\n" in column_name
        for column_name in column_names
    ):
        raise ValueError(
            "CSV header column names must be non-empty and unpadded; "
            f"archive_path={archive_path}"
        )
    if len(set(column_names)) != len(column_names):
        raise ValueError(
            f"CSV header column names must be unique; archive_path={archive_path}"
        )
    return column_names


class _Csv7zBatchStream(Iterator[pa.RecordBatch]):
    """Own one extraction process and expose its Arrow record batches."""

    def __init__(self, archive_path: Path) -> None:
        self._archive_path = archive_path
        self._member_name = archive_path.name.removesuffix(".7z")
        self._executable: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout: io.BufferedReader | None = None
        self._reader: arrow_csv.CSVStreamingReader | None = None
        self._extraction_verified = False

    def _open(self) -> None:
        for candidate in ("7zz", "7za", "7z"):
            executable = shutil.which(candidate)
            if executable is not None:
                self._executable = executable
                break
        if self._executable is None:
            raise RuntimeError(
                "7z-compatible CLI not found; install one of [7zz, 7za, 7z] "
                "and ensure it is on PATH"
            )

        try:
            self._process = subprocess.Popen(
                [
                    self._executable,
                    "x",
                    "-so",
                    "-spd",
                    "-bd",
                    "-bb0",
                    "-bso0",
                    "-bsp0",
                    "-bse2",
                    "--",
                    str(self._archive_path),
                    self._member_name,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeError(
                "failed to start 7z extraction; "
                f"executable={self._executable} "
                f"archive_path={self._archive_path} "
                f"member_name={self._member_name}"
            ) from exc

        self._stdout = cast(io.BufferedReader, self._process.stdout)
        try:
            header_bytes = self._stdout.readline(_MAX_HEADER_BYTES + 1)
        except OSError as exc:
            raise RuntimeError(
                f"failed to read CSV header; archive_path={self._archive_path}"
            ) from exc

        if not header_bytes:
            self._verify_extraction()
            raise ValueError(
                "CSV payload must contain a header row; "
                f"archive_path={self._archive_path} "
                f"member_name={self._member_name}"
            )

        column_names = _parse_csv_header(
            header_bytes,
            archive_path=self._archive_path,
        )
        try:
            has_body = bool(self._stdout.peek(1))
        except OSError as exc:
            raise RuntimeError(
                f"failed to read CSV body; archive_path={self._archive_path}"
            ) from exc
        if not has_body:
            return

        body_stream = _Utf8BomRejectingStream(
            self._stdout,
            archive_path=self._archive_path,
        )
        try:
            self._reader = arrow_csv.open_csv(
                body_stream,
                read_options=arrow_csv.ReadOptions(
                    autogenerate_column_names=False,
                    column_names=column_names,
                ),
                parse_options=arrow_csv.ParseOptions(
                    delimiter=",",
                    quote_char='"',
                    double_quote=True,
                    escape_char=False,
                    newlines_in_values=False,
                    ignore_empty_lines=False,
                ),
                convert_options=arrow_csv.ConvertOptions(
                    column_types={name: pa.string() for name in column_names},
                    strings_can_be_null=True,
                    null_values=list(_NULL_VALUES),
                    quoted_strings_can_be_null=True,
                ),
            )
        except pa.ArrowInvalid as exc:
            raise ValueError(
                "CSV body is structurally invalid; "
                f"archive_path={self._archive_path}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"failed to read CSV body; archive_path={self._archive_path}"
            ) from exc

    def __next__(self) -> pa.RecordBatch:
        if self._extraction_verified:
            raise StopIteration
        if self._reader is None:
            self._verify_extraction()
            raise StopIteration

        try:
            return next(self._reader)
        except StopIteration:
            self._verify_extraction()
            raise
        except pa.ArrowInvalid as exc:
            raise ValueError(
                "CSV body is structurally invalid; "
                f"archive_path={self._archive_path}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"failed to read CSV body; archive_path={self._archive_path}"
            ) from exc

    def _verify_extraction(self) -> None:
        process = cast("subprocess.Popen[bytes]", self._process)
        try:
            return_code = process.wait(timeout=_PROCESS_REAP_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                "failed to obtain 7z exit status; "
                f"archive_path={self._archive_path} "
                f"member_name={self._member_name}"
            ) from exc
        if return_code != 0:
            raise RuntimeError(
                "7z extraction failed; "
                f"executable={self._executable} "
                f"archive_path={self._archive_path} "
                f"member_name={self._member_name} "
                f"return_code={return_code}"
            )
        self._extraction_verified = True

    def _close(self, primary_error: BaseException | None = None) -> None:
        cleanup_errors: list[Exception] = []
        process = self._process

        if process is not None and not self._extraction_verified:
            process_is_running = True
            try:
                process_is_running = process.poll() is None
            except (OSError, subprocess.SubprocessError) as exc:
                cleanup_errors.append(exc)
            if process_is_running:
                try:
                    process.kill()
                except (OSError, subprocess.SubprocessError) as exc:
                    cleanup_errors.append(exc)

        if self._reader is not None:
            try:
                self._reader.close()
            except (OSError, pa.ArrowException) as exc:
                cleanup_errors.append(exc)
        if self._stdout is not None:
            try:
                self._stdout.close()
            except OSError as exc:
                cleanup_errors.append(exc)

        if process is not None and not self._extraction_verified:
            try:
                process.wait(timeout=_PROCESS_REAP_TIMEOUT_SECONDS)
            except (OSError, subprocess.SubprocessError) as exc:
                cleanup_errors.append(exc)

        if not cleanup_errors:
            return
        if primary_error is not None:
            for cleanup_error in cleanup_errors:
                primary_error.add_note(
                    f"CSV 7z cleanup also failed: {cleanup_error!r}"
                )
            return

        cleanup_failure = RuntimeError(
            f"failed to release CSV 7z reader; archive_path={self._archive_path}"
        )
        for cleanup_error in cleanup_errors:
            cleanup_failure.add_note(repr(cleanup_error))
        raise cleanup_failure from cleanup_errors[0]


@contextmanager
def open_csv7z_batches(
    archive_path: Path,
) -> Iterator[Iterator[pa.RecordBatch]]:
    """Yield a scoped, single-use iterator for the archive's exact CSV member.

    Usage::

        with open_csv7z_batches(Path("ticks.csv.7z")) as batches:
            for batch in batches:
                print(batch.num_rows)
    """
    if not isinstance(archive_path, Path):
        raise TypeError("field 'archive_path' must be a pathlib.Path")
    if not archive_path.name.endswith(".csv.7z"):
        raise ValueError(
            "field 'archive_path' must identify a source-native .csv.7z file"
        )
    if not archive_path.is_file():
        raise FileNotFoundError(f"archive file does not exist: {archive_path}")

    stream = _Csv7zBatchStream(archive_path)
    try:
        stream._open()
        yield stream
    except BaseException as exc:
        stream._close(primary_error=exc)
        raise
    else:
        stream._close()


__all__ = ["open_csv7z_batches"]
