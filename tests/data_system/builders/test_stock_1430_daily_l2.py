# filepath: tests/data_system/builders/test_stock_1430_daily_l2.py
"""Hand-calculated H04 fusion and persisted-input boundary tests."""

from __future__ import annotations

from datetime import date

import pyarrow as pa
import pytest

from src.data_system.builders.stock_1430 import STOCK_1430_FEATURE_SCHEMA
from src.data_system.builders.stock_1430_daily_l2 import (
    build_stock_1430_daily_l2_features,
)

_DAILY_COLUMNS = (
    "f_d_close_return_1d",
    "f_d_open_gap_1d",
    "f_d_log_amount",
    "f_d_max_drawdown_20d_asof_tminus1",
    "f_d_close_volatility_60d_asof_tminus1",
    "f_d_close_return_5d_asof_tminus1",
    "f_d_turnover_rate_mean_20d_asof_tminus1",
)


def _l2() -> pa.Table:
    arrays = [
        pa.array(["000001", "000002", "000003", "000004", "000005"]),
        pa.array(["2026-05-06"] * 5),
        pa.array([1778049000000000] * 5, type=pa.int64()),
    ]
    for i, field in enumerate(tuple(STOCK_1430_FEATURE_SCHEMA)[3:]):
        arrays.append(
            pa.array(
                [
                    None if field.nullable and row == i % 5 else row / 5
                    for row in range(5)
                ],
                type=pa.float64(),
            )
        )
    return pa.Table.from_arrays(arrays, schema=STOCK_1430_FEATURE_SCHEMA)


def _daily() -> pa.Table:
    values = (
        [10, 20, 20, None, 15],
        [-2, 0, 2, 4, 999],
        [8, None, None, None, 1000],
        [-0.2, -0.2, None, None, -0.1],
        [None, None, None, None, 100],
        [0, -2, None, 1, 0],
        [None, 3, 2, 1, 3],
    )
    return pa.table(
        {
            "symbol": pa.array(
                ["000001", "000002", "000003", "000004", "999999"],
                type=pa.large_string(),
            ),
            "trade_date": pa.array(["2026-04-30"] * 5, type=pa.large_string()),
            **{
                column: pa.array(value, type=pa.float64())
                for column, value in zip(_DAILY_COLUMNS, values, strict=True)
            },
        }
    )


def test_fusion_preserves_l2_and_ranks_each_mapped_column_in_target_universe() -> None:
    l2 = _l2().replace_schema_metadata({b"origin": b"H03"})
    daily = _daily()
    before_l2, before_daily = l2.to_pylist(), daily.to_pylist()
    result = build_stock_1430_daily_l2_features(
        l2, daily, trade_date=date(2026, 5, 6), previous_trade_date=date(2026, 4, 30)
    )

    assert result.num_columns == 42
    assert result.num_rows == 5
    assert result.select(l2.column_names).equals(l2)
    assert result.column_names[35:] == [f"{column}_rank" for column in _DAILY_COLUMNS]
    expected = (
        [1 / 3, 5 / 6, 5 / 6, None, None],
        [1 / 4, 1 / 2, 3 / 4, 1, None],
        [1, None, None, None, None],
        [0.75, 0.75, None, None, None],
        [None, None, None, None, None],
        [2 / 3, 1 / 3, None, 1, None],
        [None, 1, 2 / 3, 1 / 3, None],
    )
    for column, values in zip(_DAILY_COLUMNS, expected, strict=True):
        assert result.column(f"{column}_rank").to_pylist() == values
        assert result.schema.field(f"{column}_rank") == pa.field(
            f"{column}_rank", pa.float64(), nullable=True
        )
    assert l2.to_pylist() == before_l2
    assert daily.to_pylist() == before_daily


@pytest.mark.parametrize("missing", (None, float("nan")))
def test_fusion_preserves_all_missing_daily_values(missing: float | None) -> None:
    daily = _daily()
    for column in _DAILY_COLUMNS:
        daily = daily.set_column(
            daily.schema.get_field_index(column),
            column,
            pa.array([missing] * daily.num_rows, type=pa.float64()),
        )
    result = build_stock_1430_daily_l2_features(
        _l2(), daily, trade_date=date(2026, 5, 6), previous_trade_date=date(2026, 4, 30)
    )
    assert result.num_rows == 5
    assert all(
        result.column(f"{column}_rank").null_count == 5 for column in _DAILY_COLUMNS
    )


def test_fusion_ignores_daily_row_order_and_unconsumed_columns() -> None:
    daily = _daily()
    changed = daily.take(pa.array([4, 2, 0, 3, 1])).append_column(
        "unconsumed", pa.array(["not a factor"] * 5)
    )
    results = [
        build_stock_1430_daily_l2_features(
            _l2(),
            table,
            trade_date=date(2026, 5, 6),
            previous_trade_date=date(2026, 4, 30),
        )
        for table in (daily, changed)
    ]
    assert results[0].equals(results[1])


def test_fusion_returns_fixed_schema_when_l2_is_empty() -> None:
    result = build_stock_1430_daily_l2_features(
        _l2().slice(0, 0),
        _daily(),
        trade_date=date(2026, 5, 6),
        previous_trade_date=date(2026, 4, 30),
    )
    assert result.num_rows == 0
    assert result.num_columns == 42


@pytest.mark.parametrize(
    ("invalid", "message"),
    (
        ("missing", "must exist exactly once"),
        ("duplicate_column", "must exist exactly once"),
        ("type", "float64"),
        ("duplicate_key", "unique"),
        ("null_key", "columns must contain only non-empty strings"),
        ("empty_key", "columns must contain only non-empty strings"),
        ("date", "P partition"),
        ("empty", "at least one row"),
        ("positive_infinity", "infinity"),
        ("negative_infinity", "infinity"),
    ),
)
def test_fusion_rejects_invalid_daily_inputs(invalid: str, message: str) -> None:
    daily = _daily()
    column = _DAILY_COLUMNS[0]
    if invalid == "missing":
        daily = daily.drop([column])
    elif invalid == "duplicate_column":
        daily = daily.append_column(column, daily.column(column))
    elif invalid == "type":
        daily = daily.set_column(2, column, pa.array(["1"] * 5))
    elif invalid == "duplicate_key":
        daily = pa.concat_tables([daily, daily.slice(0, 1)])
    elif invalid in ("null_key", "empty_key"):
        daily = daily.set_column(
            0,
            "symbol",
            pa.array([None if invalid == "null_key" else ""] * 5, type=pa.string()),
        )
    elif invalid == "date":
        daily = daily.set_column(1, "trade_date", pa.array(["2026-05-06"] * 5))
    elif invalid == "empty":
        daily = daily.slice(0, 0)
    else:
        infinity = float("inf") if invalid == "positive_infinity" else -float("inf")
        daily = daily.set_column(2, column, pa.array([infinity] * 5, type=pa.float64()))

    with pytest.raises(ValueError, match=message):
        build_stock_1430_daily_l2_features(
            _l2(),
            daily,
            trade_date=date(2026, 5, 6),
            previous_trade_date=date(2026, 4, 30),
        )


@pytest.mark.parametrize(
    ("invalid", "message"),
    (
        ("schema", "schema"),
        ("columns_order", "schema"),
        ("rows_order", "canonical order"),
        ("duplicate", "unique"),
        ("date", "trade_date does not match"),
        ("decision", "decision_ts_utc does not match"),
        ("null", "symbol"),
    ),
)
def test_fusion_rejects_invalid_l2_inputs(invalid: str, message: str) -> None:
    l2 = _l2()
    if invalid == "schema":
        l2 = l2.drop([l2.column_names[-1]])
    elif invalid == "columns_order":
        l2 = l2.select(list(reversed(l2.column_names)))
    elif invalid == "rows_order":
        l2 = l2.take(pa.array([4, 3, 2, 1, 0]))
    elif invalid == "duplicate":
        l2 = pa.concat_tables([l2, l2.slice(0, 1)])
    else:
        index, values = {
            "date": (1, ["2026-05-07"] * 5),
            "decision": (2, [1] * 5),
            "null": (0, [None] * 5),
        }[invalid]
        field = l2.schema.field(index)
        l2 = l2.set_column(index, field, pa.array(values, type=field.type))

    with pytest.raises(ValueError, match=message):
        build_stock_1430_daily_l2_features(
            l2,
            _daily(),
            trade_date=date(2026, 5, 6),
            previous_trade_date=date(2026, 4, 30),
        )
