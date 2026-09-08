# filepath: tests/utils/test_parquet_writer.py
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import src.utils.parquet_writer as parquet_writer_module
from src.utils.parquet_writer import write_parquet_atomic


def test_write_parquet_atomic_writes_single_table(tmp_path: Path) -> None:
    output_file = tmp_path / "daily" / "data.parquet"
    table = pa.table({"symbol": ["600001.SH"], "close": [10.5]})

    result = write_parquet_atomic(output_file=output_file, table=table)

    assert result is None
    assert pq.read_table(output_file).to_pydict() == table.to_pydict()


def test_write_parquet_atomic_uses_pyarrow_default_row_groups(
    tmp_path: Path,
) -> None:
    output_file = tmp_path / "row_group.parquet"
    table = pa.table({"value": pa.array(range(1_048_577), type=pa.int32())})

    write_parquet_atomic(output_file=output_file, table=table)

    parquet_file = pq.ParquetFile(output_file)
    assert [
        parquet_file.metadata.row_group(index).num_rows
        for index in range(parquet_file.metadata.num_row_groups)
    ] == [1_048_576, 1]
    assert parquet_file.read().to_pydict() == table.to_pydict()


def test_write_parquet_atomic_publishes_empty_table(tmp_path: Path) -> None:
    output_file = tmp_path / "empty.parquet"
    empty_table = pa.table({"symbol": pa.array([], type=pa.string())})

    write_parquet_atomic(output_file=output_file, table=empty_table)

    written_table = pq.read_table(output_file)
    assert written_table.schema == empty_table.schema
    assert written_table.num_rows == 0


def test_write_parquet_atomic_preserves_destination_when_encoding_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_file = tmp_path / "data.parquet"
    original = pa.table({"value": [1]})
    pq.write_table(original, output_file)

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise pa.ArrowInvalid("encoding failed")

    monkeypatch.setattr(parquet_writer_module.pq, "write_table", fail_write)

    with pytest.raises(pa.ArrowInvalid, match="encoding failed"):
        write_parquet_atomic(
            output_file=output_file,
            table=pa.table({"value": [2]}),
        )

    assert pq.read_table(output_file).to_pydict() == original.to_pydict()
    assert list(tmp_path.glob(".*.tmp")) == []
