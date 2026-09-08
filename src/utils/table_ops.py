# filepath: src/utils/table_ops.py
"""Backend-independent read-only predicates for supported table types."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

import pandas as pd
import pyarrow as pa

from src.utils import _arrow_table_ops as arrow_ops
from src.utils import _pandas_table_ops as pandas_ops

Table: TypeAlias = pa.Table | pd.DataFrame

__all__ = (
    "require_columns",
    "require_finite",
    "require_non_null",
    "require_nonempty",
    "require_nonempty_strings",
    "require_positive",
    "require_unique",
)


def require_columns(
    data: Table,
    columns: Sequence[str],
    *,
    who: str,
) -> None:
    """Require every selected column name to occur exactly once.

    Example:
        from src.utils import table_ops

        frame = pd.DataFrame({"symbol": ["600000"]})
        table_ops.require_columns(frame, ("symbol",), who="daily_bar")
    """
    _require_supported_data(data)
    validated_who = _validated_who(who)
    validated_columns = _validated_columns(columns)
    _require_columns_once(data, validated_columns, who=validated_who)


def require_nonempty(
    data: Table,
    *,
    who: str,
) -> None:
    """Require a supported table to contain at least one row.

    Example:
        from src.utils import table_ops

        frame = pd.DataFrame({"symbol": ["600000"]})
        table_ops.require_nonempty(frame, who="daily_bar")
    """
    _require_supported_data(data)
    validated_who = _validated_who(who)
    rows = data.num_rows if isinstance(data, pa.Table) else len(data)
    if rows == 0:
        raise ValueError(f"{validated_who}: data must contain at least one row")


def require_non_null(
    data: Table,
    columns: Sequence[str],
    *,
    who: str,
) -> None:
    """Require selected columns to contain no logical missing values.

    Example:
        from src.utils import table_ops

        frame = pd.DataFrame({"symbol": ["600000"]})
        table_ops.require_non_null(frame, ("symbol",), who="daily_bar")
    """
    _require_supported_data(data)
    validated_who = _validated_who(who)
    validated_columns = _validated_columns(columns)
    _require_columns_once(data, validated_columns, who=validated_who)
    if isinstance(data, pa.Table):
        failing_columns = arrow_ops._columns_with_null(data, validated_columns)
    else:
        failing_columns = pandas_ops._columns_with_null(data, validated_columns)
    if failing_columns:
        raise ValueError(
            f"{validated_who}: columns must not contain null values: "
            f"{list(failing_columns)!r}"
        )


def require_nonempty_strings(
    data: Table,
    columns: Sequence[str],
    *,
    who: str,
) -> None:
    """Require selected values to be real strings other than ``""``.

    Example:
        from src.utils import table_ops

        table = pa.table({"symbol": ["600000"]})
        table_ops.require_nonempty_strings(table, ("symbol",), who="daily_bar")
    """
    _require_supported_data(data)
    validated_who = _validated_who(who)
    validated_columns = _validated_columns(columns)
    _require_columns_once(data, validated_columns, who=validated_who)
    if isinstance(data, pa.Table):
        failing_columns = arrow_ops._columns_without_nonempty_strings(
            data,
            validated_columns,
        )
    else:
        failing_columns = pandas_ops._columns_without_nonempty_strings(
            data,
            validated_columns,
        )
    if failing_columns:
        raise ValueError(
            f"{validated_who}: columns must contain only non-empty strings: "
            f"{list(failing_columns)!r}"
        )


def require_unique(
    data: Table,
    columns: Sequence[str],
    *,
    who: str,
) -> None:
    """Require selected columns to form one logical composite key.

    Example:
        from src.utils import table_ops

        frame = pd.DataFrame({"symbol": ["600000", "000001"]})
        table_ops.require_unique(frame, ("symbol",), who="daily_bar")
    """
    _require_supported_data(data)
    validated_who = _validated_who(who)
    validated_columns = _validated_columns(columns)
    _require_columns_once(data, validated_columns, who=validated_who)
    if isinstance(data, pa.Table):
        unsupported_columns = arrow_ops._unsupported_key_columns(
            data,
            validated_columns,
        )
    else:
        unsupported_columns = pandas_ops._unsupported_key_columns(
            data,
            validated_columns,
        )
    if unsupported_columns:
        raise ValueError(
            f"{validated_who}: key columns must contain only strings, booleans, "
            f"integers, floats, or nulls: {list(unsupported_columns)!r}"
        )

    if isinstance(data, pa.Table):
        has_duplicates = arrow_ops._has_duplicate_keys(data, validated_columns)
    else:
        has_duplicates = pandas_ops._has_duplicate_keys(data, validated_columns)
    if has_duplicates:
        raise ValueError(
            f"{validated_who}: columns must form a unique key: "
            f"{list(validated_columns)!r}"
        )


def require_finite(
    data: Table,
    columns: Sequence[str],
    *,
    who: str,
) -> None:
    """Require selected values to be finite integers or floats.

    Example:
        from src.utils import table_ops

        table = pa.table({"close": [10.0]})
        table_ops.require_finite(table, ("close",), who="daily_bar")
    """
    _require_supported_data(data)
    validated_who = _validated_who(who)
    validated_columns = _validated_columns(columns)
    _require_columns_once(data, validated_columns, who=validated_who)
    if isinstance(data, pa.Table):
        failing_columns = arrow_ops._columns_without_finite_numbers(
            data,
            validated_columns,
        )
    else:
        failing_columns = pandas_ops._columns_without_finite_numbers(
            data,
            validated_columns,
        )
    if failing_columns:
        raise ValueError(
            f"{validated_who}: columns must contain only finite numbers: "
            f"{list(failing_columns)!r}"
        )


def require_positive(
    data: Table,
    columns: Sequence[str],
    *,
    who: str,
) -> None:
    """Require selected values to be positive finite integers or floats.

    Example:
        from src.utils import table_ops

        frame = pd.DataFrame({"close": [10.0]})
        table_ops.require_positive(frame, ("close",), who="daily_bar")
    """
    _require_supported_data(data)
    validated_who = _validated_who(who)
    validated_columns = _validated_columns(columns)
    _require_columns_once(data, validated_columns, who=validated_who)
    if isinstance(data, pa.Table):
        failing_columns = arrow_ops._columns_without_positive_numbers(
            data,
            validated_columns,
        )
    else:
        failing_columns = pandas_ops._columns_without_positive_numbers(
            data,
            validated_columns,
        )
    if failing_columns:
        raise ValueError(
            f"{validated_who}: columns must contain only positive finite numbers: "
            f"{list(failing_columns)!r}"
        )


def _require_supported_data(data: object) -> None:
    if not isinstance(data, (pa.Table, pd.DataFrame)):
        raise TypeError("data must be a pyarrow.Table or pandas.DataFrame")


def _validated_who(who: object) -> str:
    if not isinstance(who, str):
        raise TypeError("who must be a string")
    if who == "":
        raise ValueError("who must not be empty")
    return who


def _validated_columns(columns: object) -> tuple[str, ...]:
    if isinstance(columns, (str, bytes)) or not isinstance(columns, Sequence):
        raise TypeError("columns must be a sequence of strings")
    validated_columns = tuple(columns)
    if any(not isinstance(column, str) for column in validated_columns):
        raise TypeError("columns must be a sequence of strings")
    if not validated_columns:
        raise ValueError("columns must not be empty")
    if len(set(validated_columns)) != len(validated_columns):
        raise ValueError("columns must not contain duplicates")
    return validated_columns


def _require_columns_once(
    data: Table,
    columns: tuple[str, ...],
    *,
    who: str,
) -> None:
    if isinstance(data, pa.Table):
        invalid_columns = arrow_ops._invalid_columns(data, columns)
    else:
        invalid_columns = pandas_ops._invalid_columns(data, columns)
    if invalid_columns:
        raise ValueError(
            f"{who}: columns must exist exactly once: {list(invalid_columns)!r}"
        )
