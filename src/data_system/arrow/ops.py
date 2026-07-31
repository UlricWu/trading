# filepath: src/data_system/arrow/ops.py
"""Shared Arrow compute helpers for data-system builders and engines."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeAlias

import pyarrow as pa
import pyarrow.compute as pc

ArrowArray: TypeAlias = pa.Array | pa.ChunkedArray


def sort_by(table: pa.Table, columns: Sequence[str]) -> pa.Table:
    """Return `table` sorted by `columns` in ascending order."""

    return table.take(
        pc.sort_indices(
            table,
            sort_keys=[(column, "ascending") for column in columns],
        )
    )


def append_or_replace(
    table: pa.Table,
    name: str,
    values: ArrowArray,
) -> pa.Table:
    """Append `name` to `table`, or replace an existing column with that name."""

    if name in table.column_names:
        return table.set_column(table.column_names.index(name), name, values)
    return table.append_column(name, values)


def map_values_or_null(
    values: ArrowArray,
    mapping: Mapping[object, object],
) -> ArrowArray:
    """Map Arrow values through a Python mapping and return null for misses."""

    if not mapping:
        return pa.nulls(len(values))

    keys = pa.array(list(mapping.keys()))
    mapped_values = pa.array(list(mapping.values()))
    indexes = pc.index_in(values, keys)
    miss = pc.less(indexes, 0)
    safe_indexes = pc.if_else(miss, pa.nulls(len(values), type=pa.int32()), indexes)
    return pc.take(mapped_values, safe_indexes)


def zeros_i64(length: int) -> pa.Array:
    """Return an int64 Arrow array filled with zeros."""

    return pa.array([0] * length, type=pa.int64())
