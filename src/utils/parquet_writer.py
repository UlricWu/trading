# filepath: src/utils/parquet_writer.py
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.utils.filesystem import FileSystem


def write_parquet_atomic(
    *,
    output_file: Path,
    table: pa.Table,
) -> None:
    """Write one Arrow table atomically using PyArrow's default row groups."""
    with FileSystem.atomic_path(output_file) as temporary_file:
        pq.write_table(
            table,
            temporary_file,
            use_dictionary=True,
            compression="zstd",
            write_statistics=True,
        )


__all__ = ["write_parquet_atomic"]
