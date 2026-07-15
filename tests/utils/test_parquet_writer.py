# filepath: tests/utils/test_parquet_writer.py
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import ParquetAppendWriter, write_arrow_table_parquet


def test_parquet_append_writer_writes_single_table(tmp_path: Path) -> None:
    output_file = tmp_path / "daily" / "data.parquet"
    table = pa.table({"symbol": ["600001.SH"], "close": [10.5]})

    writer = ParquetAppendWriter(output_file=output_file)
    writer.write(table)
    written = writer.close()

    assert written == output_file
    assert output_file.exists()
    assert pq.read_table(output_file).to_pydict() == table.to_pydict()


def test_write_arrow_table_parquet_writes_single_table(tmp_path: Path) -> None:
    output_file = tmp_path / "helper" / "data.parquet"
    table = pa.table({"symbol": ["600001.SH"], "close": [10.5]})

    written = write_arrow_table_parquet(output_file=output_file, table=table)

    assert written == output_file
    assert output_file.exists()
    assert pq.read_table(output_file).to_pydict() == table.to_pydict()


def test_parquet_append_writer_rejects_schema_mismatch(tmp_path: Path) -> None:
    output_file = tmp_path / "data.parquet"
    left = pa.table({"symbol": ["600001.SH"]})
    right = pa.table({"symbol": ["600001.SH"], "close": [10.5]})

    with (
        pytest.raises(ValueError, match="schema mismatch"),
        ParquetAppendWriter(output_file=output_file) as writer,
    ):
        writer.write(left)
        writer.write(right)

    assert not output_file.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_parquet_append_writer_chunked_write_preserves_rows(tmp_path: Path) -> None:
    output_file = tmp_path / "chunked.parquet"
    table = pa.table(
        {
            "symbol": ["600001.SH", "600002.SH", "600003.SH"],
            "close": [10.5, 11.0, 12.3],
        }
    )

    writer = ParquetAppendWriter(output_file=output_file)
    writer.write(table, max_rows_per_chunk=1)
    writer.close()

    assert pq.read_table(output_file).to_pydict() == table.to_pydict()


def test_write_arrow_table_parquet_supports_chunked_write(tmp_path: Path) -> None:
    output_file = tmp_path / "helper_chunked.parquet"
    table = pa.table({"symbol": ["a", "b", "c"], "close": [1.0, 2.0, 3.0]})

    write_arrow_table_parquet(
        output_file=output_file,
        table=table,
        max_rows_per_chunk=1,
    )

    assert pq.read_table(output_file).to_pydict() == table.to_pydict()


def test_write_arrow_table_parquet_honors_row_group_size(tmp_path: Path) -> None:
    output_file = tmp_path / "helper_row_group.parquet"
    table = pa.table({"symbol": ["a", "b", "c", "d", "e"], "close": [1, 2, 3, 4, 5]})

    write_arrow_table_parquet(
        output_file=output_file,
        table=table,
        row_group_size=2,
    )

    parquet_file = pq.ParquetFile(output_file)
    assert parquet_file.metadata.num_row_groups == 3
    assert [parquet_file.metadata.row_group(i).num_rows for i in range(3)] == [2, 2, 1]
    assert parquet_file.read().to_pydict() == table.to_pydict()


def test_write_arrow_table_parquet_publishes_empty_table(tmp_path: Path) -> None:
    output_file = tmp_path / "empty.parquet"
    empty_table = pa.table({"symbol": pa.array([], type=pa.string())})

    write_arrow_table_parquet(output_file=output_file, table=empty_table)

    assert output_file.is_file()
    written_table = pq.read_table(output_file)
    assert written_table.schema == empty_table.schema
    assert written_table.num_rows == 0


def test_parquet_append_writers_use_independent_temporary_files(
    tmp_path: Path,
) -> None:
    output_file = tmp_path / "shared.parquet"
    first_table = pa.table({"value": [1]})
    second_table = pa.table({"value": [2]})
    first_writer = ParquetAppendWriter(output_file=output_file)
    second_writer = ParquetAppendWriter(output_file=output_file)

    first_writer.write(first_table)
    second_writer.write(second_table)
    first_writer.close()
    second_writer.close()

    assert pq.read_table(output_file).to_pydict() == second_table.to_pydict()
    assert list(tmp_path.glob("*.tmp")) == []


def test_parquet_append_writer_does_not_report_success_after_publish_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_file = tmp_path / "failed.parquet"
    writer = ParquetAppendWriter(output_file=output_file)
    writer.write(pa.table({"value": [1]}))

    def fail_publish(
        _staged_path: str | Path,
        _destination_path: str | Path,
    ) -> Path:
        raise OSError("publish failed")

    monkeypatch.setattr(FileSystem, "publish_file_atomic", fail_publish)

    with pytest.raises(OSError, match="publish failed"):
        writer.close()
    with pytest.raises(RuntimeError, match="without publishing"):
        writer.close()

    assert not output_file.exists()
    assert list(tmp_path.glob("*.tmp")) == []
