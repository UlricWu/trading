# filepath: src/utils/csv7z_batch_source.py
"""Stream Arrow record batches from one source-native ``.csv.7z`` file."""

from __future__ import annotations

import csv as standard_csv
import io
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as arrow_csv

from src.utils.seven_zip import open_extract_stdout

_CSV_BLOCK_SIZE_BYTES = 128 * 1024 * 1024
_MAX_HEADER_BYTES = 1024 * 1024
_PROCESS_TERMINATION_TIMEOUT_SECONDS = 5.0


class _Csv7zReader:
    """Own one Arrow streaming reader and its decompressor process."""

    def __init__(
        self,
        reader: arrow_csv.CSVStreamingReader,
        process: subprocess.Popen[bytes],
        archive_path: Path,
    ) -> None:
        self._reader = reader
        self._process = process
        self._archive_path = archive_path
        self._is_closed = False

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        return iter(self._reader)

    def close(self, *, verify_process_exit: bool) -> None:
        """Release resources and optionally translate a decompressor failure."""
        if self._is_closed:
            return
        self._is_closed = True

        cleanup_errors: list[Exception] = []
        try:
            self._reader.close()
        except Exception as exc:
            cleanup_errors.append(exc)

        stdout = self._process.stdout
        if stdout is not None:
            try:
                stdout.close()
            except OSError as exc:
                cleanup_errors.append(exc)

        try:
            if verify_process_exit:
                return_code = self._process.wait()
                if return_code != 0:
                    cleanup_errors.append(
                        RuntimeError(
                            "7z extraction failed; "
                            f"archive_path={self._archive_path} "
                            f"return_code={return_code}"
                        )
                    )
            else:
                self._terminate_process()
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup_errors.append(exc)

        stderr = self._process.stderr
        if stderr is not None:
            try:
                stderr.close()
            except OSError as exc:
                cleanup_errors.append(exc)

        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        if cleanup_errors:
            raise ExceptionGroup("CSV 7z reader cleanup failed", cleanup_errors)

    def _terminate_process(self) -> None:
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=_PROCESS_TERMINATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()


class Csv7zBatchSource:
    """Validate and stream a source-native ``.csv.7z`` payload as Arrow batches."""

    def __init__(self, archive_path: Path) -> None:
        if not isinstance(archive_path, Path):
            raise TypeError("field 'archive_path' must be a pathlib.Path")
        if not archive_path.is_file():
            raise FileNotFoundError(f"archive file does not exist: {archive_path}")
        if not archive_path.name.endswith(".csv.7z"):
            raise ValueError(
                "field 'archive_path' must identify a source-native .csv.7z file"
            )

        self._archive_path = archive_path

    def __iter__(self) -> Iterator[pa.RecordBatch]:
        """Yield all batches and release the process on every exit path."""
        owned_reader = self._open_reader()
        try:
            yield from owned_reader
        except BaseException as exc:
            try:
                owned_reader.close(verify_process_exit=False)
            except Exception as cleanup_error:
                exc.add_note(f"CSV 7z cleanup also failed: {cleanup_error!r}")
            raise
        else:
            owned_reader.close(verify_process_exit=True)

    def _open_reader(self) -> _Csv7zReader:
        process = open_extract_stdout(self._archive_path)
        stdout = process.stdout
        if stdout is None:
            self._terminate_failed_open(process)
            raise RuntimeError("7z extraction process did not expose stdout")

        try:
            header_bytes = stdout.readline(_MAX_HEADER_BYTES + 1)
            if len(header_bytes) > _MAX_HEADER_BYTES:
                raise ValueError("CSV header exceeds the maximum supported size")
            if not header_bytes:
                return_code = process.wait()
                if return_code != 0:
                    raise RuntimeError(
                        "7z extraction failed before the CSV header; "
                        f"archive_path={self._archive_path} "
                        f"return_code={return_code}"
                    )
            column_names = self._parse_header(header_bytes)
            convert_options = arrow_csv.ConvertOptions(
                column_types={name: pa.string() for name in column_names},
                strings_can_be_null=True,
                null_values=["", " ", "NULL", "N/A", "nan"],
                quoted_strings_can_be_null=True,
            )
            read_options = arrow_csv.ReadOptions(
                autogenerate_column_names=False,
                column_names=column_names,
                block_size=_CSV_BLOCK_SIZE_BYTES,
                use_threads=True,
            )
            reader = arrow_csv.open_csv(
                stdout,
                read_options=read_options,
                convert_options=convert_options,
            )
        except BaseException as exc:
            try:
                self._terminate_failed_open(process)
            except Exception as cleanup_error:
                exc.add_note(f"CSV 7z open cleanup also failed: {cleanup_error!r}")
            raise

        return _Csv7zReader(reader, process, self._archive_path)

    @staticmethod
    def _parse_header(header_bytes: bytes) -> list[str]:
        if not header_bytes:
            raise ValueError("CSV payload must contain a header row")
        try:
            header_text = header_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV header must use UTF-8 encoding") from exc

        rows = list(standard_csv.reader(io.StringIO(header_text, newline="")))
        if len(rows) != 1 or not rows[0]:
            raise ValueError(
                "CSV payload must contain exactly one non-empty header row"
            )
        column_names = rows[0]
        if any(
            not column_name or column_name.strip() != column_name
            for column_name in column_names
        ):
            raise ValueError("CSV header column names must be non-empty and unpadded")
        if len(set(column_names)) != len(column_names):
            raise ValueError("CSV header column names must be unique")
        return column_names

    @staticmethod
    def _terminate_failed_open(process: subprocess.Popen[bytes]) -> None:
        stdout = process.stdout
        if stdout is not None:
            stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_PROCESS_TERMINATION_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stderr = process.stderr
        if stderr is not None:
            stderr.close()


__all__ = ["Csv7zBatchSource"]
