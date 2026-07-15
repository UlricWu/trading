# filepath: src/utils/parquet_writer.py
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Self

import pyarrow as pa
import pyarrow.parquet as pq

from src.utils.filesystem import FileSystem


class ParquetAppendWriter:
    """Write same-schema Arrow batches to one atomically published Parquet file.

    ``schema=None`` means that the first table defines the schema.
    The writer owns its temporary file and ``ParquetWriter`` resource; callers
    must use it as a context manager or call ``close()`` explicitly.
    """

    def __init__(
        self,
        *,
        output_file: Path,
        schema: pa.Schema | None = None,
    ) -> None:
        if not isinstance(output_file, Path):
            raise TypeError("field 'output_file' must be a pathlib.Path")
        if schema is not None and not isinstance(schema, pa.Schema):
            raise TypeError("field 'schema' must be a pyarrow.Schema or None")

        self._final_path = output_file
        self._temporary_path: Path | None = None
        self._schema = schema
        self._writer: pq.ParquetWriter | None = None
        self._rows = 0
        self._is_closed = False
        self._is_published = False

    def write(
        self,
        table: pa.Table,
        *,
        max_rows_per_chunk: int | None = None,
        row_group_size: int | None = None,
    ) -> None:
        """Append one table without changing its schema or row order.

        ``max_rows_per_chunk=None`` disables input slicing. ``row_group_size``
        is forwarded to PyArrow, where ``None`` selects its default behavior.
        """
        if self._is_closed:
            raise RuntimeError("cannot write after ParquetAppendWriter.close()")
        if not isinstance(table, pa.Table):
            raise TypeError("field 'table' must be a pyarrow.Table")
        _validate_optional_positive_int(
            value=max_rows_per_chunk,
            field_name="max_rows_per_chunk",
        )
        _validate_optional_positive_int(
            value=row_group_size,
            field_name="row_group_size",
        )
        if self._writer is None:
            self._initialize_writer(table.schema)
        writer = self._writer
        if writer is None:
            raise RuntimeError("ParquetWriter initialization did not produce a writer")
        if table.schema != self._schema:
            raise ValueError(
                "Parquet schema mismatch: "
                f"expected={self._schema}; received={table.schema}"
            )

        if table.num_rows == 0:
            return

        if max_rows_per_chunk is None or table.num_rows <= max_rows_per_chunk:
            writer.write_table(table, row_group_size=row_group_size)
            self._rows += table.num_rows
            return

        start_row = 0
        while start_row < table.num_rows:
            chunk_row_count = min(
                max_rows_per_chunk,
                table.num_rows - start_row,
            )
            chunk = table.slice(start_row, chunk_row_count)
            writer.write_table(chunk, row_group_size=row_group_size)
            self._rows += chunk_row_count
            start_row += chunk_row_count

    def close(self) -> Path:
        """Close owned resources and atomically publish the completed file."""
        if self._is_closed:
            if not self._is_published:
                raise RuntimeError("ParquetAppendWriter closed without publishing")
            return self._final_path

        if self._writer is None:
            if self._schema is None:
                raise RuntimeError(
                    "cannot close ParquetAppendWriter before a schema is defined"
                )
            self._initialize_writer(self._schema)

        writer = self._writer
        self._writer = None
        try:
            if writer is not None:
                writer.close()

            temporary_path = self._temporary_path
            if temporary_path is None:
                raise RuntimeError("Parquet writer did not create a temporary file")
            with temporary_path.open("rb") as temporary_file:
                os.fsync(temporary_file.fileno())
            FileSystem.publish_file_atomic(temporary_path, self._final_path)
            self._temporary_path = None
            self._is_published = True
            self._is_closed = True
            return self._final_path
        except BaseException:
            self._remove_temporary_file()
            self._is_closed = True
            raise

    @property
    def rows(self) -> int:
        """Return the number of rows written successfully."""
        return self._rows

    def __enter__(self) -> Self:
        if self._is_closed:
            raise RuntimeError("cannot enter a closed ParquetAppendWriter")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.close()
            return
        try:
            self._abort()
        except Exception as cleanup_error:
            if exc is None:
                raise
            exc.add_note(f"Parquet writer cleanup also failed: {cleanup_error!r}")

    def _initialize_writer(self, schema: pa.Schema) -> None:
        """Establish the schema and owned temporary writer exactly once."""
        if self._schema is None:
            self._schema = schema
        elif schema != self._schema:
            raise ValueError(
                "Initial Parquet schema mismatch: "
                f"expected={self._schema}; received={schema}"
            )

        FileSystem.ensure_dir(self._final_path.parent)
        with tempfile.NamedTemporaryFile(
            dir=self._final_path.parent,
            prefix=f".{self._final_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            self._temporary_path = Path(temporary_file.name)

        try:
            self._writer = pq.ParquetWriter(
                self._temporary_path,
                self._schema,
                use_dictionary=True,
                compression="zstd",
                write_statistics=True,
            )
        except BaseException:
            self._remove_temporary_file()
            raise

    def _abort(self) -> None:
        """Release the writer and remove an unpublished partial file."""
        writer = self._writer
        self._writer = None
        try:
            if writer is not None:
                writer.close()
        finally:
            self._remove_temporary_file()
            self._is_closed = True

    def _remove_temporary_file(self) -> None:
        temporary_path = self._temporary_path
        self._temporary_path = None
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_arrow_table_parquet(
    *,
    output_file: Path,
    table: pa.Table,
    schema: pa.Schema | None = None,
    max_rows_per_chunk: int | None = None,
    row_group_size: int | None = None,
) -> Path:
    """Write one Arrow table through the same atomic writer lifecycle."""
    with ParquetAppendWriter(output_file=output_file, schema=schema) as writer:
        writer.write(
            table,
            max_rows_per_chunk=max_rows_per_chunk,
            row_group_size=row_group_size,
        )
    return output_file


def _validate_optional_positive_int(
    *,
    value: int | None,
    field_name: str,
) -> None:
    if value is None:
        return
    if type(value) is not int:
        raise TypeError(f"field '{field_name}' must be an integer or None")
    if value <= 0:
        raise ValueError(f"field '{field_name}' must be positive")


__all__ = ["ParquetAppendWriter", "write_arrow_table_parquet"]
