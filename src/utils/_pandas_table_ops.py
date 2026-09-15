# filepath: src/utils/_pandas_table_ops.py
"""Private Pandas predicates used by the common table operations."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd


def _invalid_columns(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> tuple[str, ...]:
    column_names = list(frame.columns)
    return tuple(column for column in columns if column_names.count(column) != 1)


def _columns_with_null(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        column
        for column in columns
        if any(_is_missing(value) for value in frame[column].tolist())
    )


def _columns_without_nonempty_strings(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        column
        for column in columns
        if any(
            not isinstance(value, (str, np.str_)) or value == ""
            for value in frame[column].tolist()
        )
    )


def _unsupported_key_columns(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        column
        for column in columns
        if any(_logical_key_value(value) is None for value in frame[column].tolist())
    )


def _has_duplicate_keys(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> bool:
    seen: set[tuple[tuple[str, object], ...]] = set()
    selected = frame.loc[:, list(columns)]
    for row in selected.itertuples(index=False, name=None):
        key = tuple(
            logical_value
            for value in row
            if (logical_value := _logical_key_value(value)) is not None
        )
        if key in seen:
            return True
        seen.add(key)
    return False


def _columns_without_finite_numbers(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        column
        for column in columns
        if any(not _is_finite_number(value) for value in frame[column].tolist())
    )


def _columns_without_positive_numbers(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        column
        for column in columns
        if any(
            not _is_finite_number(value) or value <= 0
            for value in frame[column].tolist()
        )
    )


def _is_missing(value: object) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    return isinstance(value, (float, np.floating)) and math.isnan(float(value))


def _is_finite_number(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return False
    if isinstance(value, (int, np.integer)):
        return True
    if isinstance(value, (float, np.floating)):
        return math.isfinite(float(value))
    return False


def _logical_key_value(value: object) -> tuple[str, object] | None:
    if _is_missing(value):
        return ("missing", None)
    if isinstance(value, (str, np.str_)):
        return ("string", str(value))
    if isinstance(value, (bool, np.bool_)):
        return ("boolean", bool(value))
    if isinstance(value, (int, np.integer)):
        return ("number", int(value))
    if isinstance(value, (float, np.floating)):
        return ("number", float(value))
    return None
