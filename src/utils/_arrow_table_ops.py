# filepath: src/utils/_arrow_table_ops.py
"""Private Arrow predicates used by the common table operations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

import pyarrow as pa
import pyarrow.compute as pc

ArrowArray: TypeAlias = pa.Array | pa.ChunkedArray


def _invalid_columns(
    table: pa.Table,
    columns: Sequence[str],
) -> tuple[str, ...]:
    return tuple(column for column in columns if table.column_names.count(column) != 1)


def _columns_with_null(
    table: pa.Table,
    columns: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        column
        for column in columns
        if _any_true(
            pc.is_null(
                _logical_values(table.column(column)),
                nan_is_null=True,
            )
        )
    )


def _columns_without_nonempty_strings(
    table: pa.Table,
    columns: Sequence[str],
) -> tuple[str, ...]:
    failing_columns = []
    for column in columns:
        values = _logical_values(table.column(column))
        if len(values) == 0:
            continue
        if not (
            pa.types.is_string(values.type) or pa.types.is_large_string(values.type)
        ):
            failing_columns.append(column)
            continue
        if _any_true(pc.is_null(values)) or _any_true(
            pc.equal(values, pa.scalar("", type=values.type))
        ):
            failing_columns.append(column)
    return tuple(failing_columns)


def _unsupported_key_columns(
    table: pa.Table,
    columns: Sequence[str],
) -> tuple[str, ...]:
    failing_columns = []
    for column in columns:
        values = _logical_values(table.column(column))
        if len(values) == values.null_count:
            continue
        if not (
            pa.types.is_string(values.type)
            or pa.types.is_large_string(values.type)
            or pa.types.is_boolean(values.type)
            or pa.types.is_integer(values.type)
            or pa.types.is_floating(values.type)
        ):
            failing_columns.append(column)
    return tuple(failing_columns)


def _has_duplicate_keys(
    table: pa.Table,
    columns: Sequence[str],
) -> bool:
    key_columns = [
        _normalized_key_values(_logical_values(table.column(column)))
        for column in columns
    ]
    key_table = pa.Table.from_arrays(key_columns, names=list(columns))
    return key_table.group_by(list(columns)).aggregate([]).num_rows != table.num_rows


def _columns_without_finite_numbers(
    table: pa.Table,
    columns: Sequence[str],
) -> tuple[str, ...]:
    failing_columns = []
    for column in columns:
        values = _logical_values(table.column(column))
        if len(values) == 0:
            continue
        if not (pa.types.is_integer(values.type) or pa.types.is_floating(values.type)):
            failing_columns.append(column)
            continue
        if _any_true(pc.is_null(values, nan_is_null=True)) or not _all_true(
            pc.is_finite(values)
        ):
            failing_columns.append(column)
    return tuple(failing_columns)


def _columns_without_positive_numbers(
    table: pa.Table,
    columns: Sequence[str],
) -> tuple[str, ...]:
    failing_columns = []
    for column in columns:
        values = _logical_values(table.column(column))
        if len(values) == 0:
            continue
        if not (pa.types.is_integer(values.type) or pa.types.is_floating(values.type)):
            failing_columns.append(column)
            continue
        comparable_values = (
            pc.cast(values, pa.float32())
            if pa.types.is_float16(values.type)
            else values
        )
        if (
            _any_true(pc.is_null(values, nan_is_null=True))
            or not _all_true(pc.is_finite(values))
            or not _all_true(
                pc.greater(
                    comparable_values,
                    pa.scalar(0, type=comparable_values.type),
                )
            )
        ):
            failing_columns.append(column)
    return tuple(failing_columns)


def _logical_values(values: ArrowArray) -> ArrowArray:
    if pa.types.is_dictionary(values.type):
        return pc.dictionary_decode(values)
    return values


def _normalized_key_values(values: ArrowArray) -> ArrowArray:
    if len(values) == values.null_count and not (
        pa.types.is_string(values.type)
        or pa.types.is_large_string(values.type)
        or pa.types.is_boolean(values.type)
        or pa.types.is_integer(values.type)
        or pa.types.is_floating(values.type)
    ):
        return pa.chunked_array([pa.nulls(len(values))])
    if not pa.types.is_floating(values.type):
        return values
    normalized = (
        pc.cast(values, pa.float32()) if pa.types.is_float16(values.type) else values
    )
    normalized = pc.if_else(
        pc.is_nan(normalized),
        pa.nulls(len(normalized), type=normalized.type),
        normalized,
    )
    return pc.if_else(
        pc.equal(normalized, pa.scalar(0, type=normalized.type)),
        pa.scalar(0, type=normalized.type),
        normalized,
    )


def _any_true(values: ArrowArray) -> bool:
    return pc.any(values).as_py() is True


def _all_true(values: ArrowArray) -> bool:
    return pc.all(values).as_py() is True
