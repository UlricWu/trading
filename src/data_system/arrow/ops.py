# filepath: src/data_system/arrow/ops.py
"""Shared Arrow compute helpers for data-system builders and engines."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pyarrow as pa
import pyarrow.compute as pc

ArrowArray = pa.Array | pa.ChunkedArray


def require_columns(
    table: pa.Table,
    columns: Sequence[str],
    *,
    who: str = "input",
) -> None:
    """Reject a table that does not contain every required column."""

    missing = [column for column in columns if column not in table.column_names]
    if missing:
        raise ValueError(f"missing {who} columns: {missing}")


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


def log1p_non_negative_or_null(values: ArrowArray) -> ArrowArray:
    """Return log1p(values), using null for null or negative inputs."""

    numeric = pc.cast(values, pa.float64())
    valid_values = pc.if_else(
        pc.greater_equal(numeric, pa.scalar(0.0, pa.float64())),
        numeric,
        pa.nulls(len(numeric), type=pa.float64()),
    )
    return pc.log1p(valid_values)


def divide_by_positive_or_null(num: ArrowArray, den: ArrowArray) -> ArrowArray:
    """Return num / den, using null when the denominator is null or non-positive."""

    numerator = pc.cast(num, pa.float64())
    denominator = pc.cast(den, pa.float64())
    positive_denominator = pc.if_else(
        pc.greater(denominator, pa.scalar(0.0, pa.float64())),
        denominator,
        pa.nulls(len(denominator), type=pa.float64()),
    )
    return pc.divide(numerator, positive_denominator)


def ratio_minus_one_or_null(num: ArrowArray, den: ArrowArray) -> ArrowArray:
    """Return num / den - 1, using null when the denominator is not positive."""

    return pc.subtract(
        divide_by_positive_or_null(num, den),
        pa.scalar(1.0, pa.float64()),
    )


def difference_over_positive_or_null(
    left: ArrowArray,
    right: ArrowArray,
    den: ArrowArray,
) -> ArrowArray:
    """Return (left - right) / den, using null when the denominator is not positive."""

    return divide_by_positive_or_null(pc.subtract(left, right), den)


def mod_i64(values: ArrowArray, divisor: int) -> ArrowArray:
    """Return `values % divisor` using Arrow arithmetic on int64 arrays."""

    if divisor <= 0:
        raise ValueError("divisor must be positive")

    divisor_scalar = pa.scalar(divisor, pa.int64())
    return pc.subtract(
        values,
        pc.multiply(
            pc.cast(pc.floor(pc.divide(values, divisor_scalar)), pa.int64()),
            divisor_scalar,
        ),
    )


def floor_to_multiple_i64(values: ArrowArray, multiple: int) -> ArrowArray:
    """Floor integer Arrow values to a positive integer multiple."""

    if multiple <= 0:
        raise ValueError("multiple must be positive")

    multiple_scalar = pa.scalar(multiple, pa.int64())
    return pc.multiply(
        pc.cast(pc.floor(pc.divide(values, multiple_scalar)), pa.int64()),
        multiple_scalar,
    )


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
