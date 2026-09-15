# filepath: tests/data_system/builders/test_level2_stock_trade_1m.py
"""Behavior tests for Level2 stock minute-fact aggregation."""

from __future__ import annotations

import math

import pyarrow as pa
import pytest

from src.data_system.builders.level2_stock_trade_1m import (
    build_level2_stock_trade_1m,
)

_MINUTE_US = 60_000_000
_OUTPUT_COLUMNS = [
    "symbol",
    "trade_date",
    "minute_start_ts_utc",
    "phase",
    "open",
    "high",
    "low",
    "close",
    "volume_sum",
    "notional_sum",
    "trade_count",
    "tick_signed_volume_sum",
    "tick_signed_notional_sum",
]


def test_builder_produces_sorted_sparse_phase_aware_ohlc_and_conservation() -> None:
    trades = _trade_table(
        symbol=["600001", "600000", "600000", "600000", "600000", "510050"],
        ts_utc=[
            2 * _MINUTE_US + 1,
            _MINUTE_US + 20,
            _MINUTE_US + 10,
            _MINUTE_US + 30,
            2 * _MINUTE_US,
            _MINUTE_US + 40,
        ],
        main_seq=[1, 2, 1, 3, 1, 1],
        sub_seq=[1, 1, 1, 1, 1, 1],
        price=[20.0, 11.0, 10.0, 9.0, 12.0, 3.0],
        volume=[5, 20, 10, 30, 40, 1_000],
        security_type=["stock", "stock", "stock", "stock", "stock", "etf"],
        phase=[2, 2, 2, 0, 2, 2],
        notional=[100.0, 222.0, 101.0, 270.0, 480.0, 3_000.0],
        trade_side=[-1, 1, 0, -1, 1, 1],
    )

    output = build_level2_stock_trade_1m(
        trades,
        trade_date="2026-05-06",
    )

    assert output.column_names == _OUTPUT_COLUMNS
    assert output.schema == pa.schema(
        [
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("trade_date", pa.string(), nullable=False),
            pa.field("minute_start_ts_utc", pa.int64(), nullable=False),
            pa.field("phase", pa.int8(), nullable=False),
            pa.field("open", pa.float64(), nullable=False),
            pa.field("high", pa.float64(), nullable=False),
            pa.field("low", pa.float64(), nullable=False),
            pa.field("close", pa.float64(), nullable=False),
            pa.field("volume_sum", pa.int64(), nullable=False),
            pa.field("notional_sum", pa.float64(), nullable=False),
            pa.field("trade_count", pa.int64(), nullable=False),
            pa.field("tick_signed_volume_sum", pa.int64(), nullable=False),
            pa.field("tick_signed_notional_sum", pa.float64(), nullable=False),
        ]
    )
    assert output.to_pylist() == [
        {
            "symbol": "600000",
            "trade_date": "2026-05-06",
            "minute_start_ts_utc": _MINUTE_US,
            "phase": 0,
            "open": 9.0,
            "high": 9.0,
            "low": 9.0,
            "close": 9.0,
            "volume_sum": 30,
            "notional_sum": 270.0,
            "trade_count": 1,
            "tick_signed_volume_sum": -30,
            "tick_signed_notional_sum": -270.0,
        },
        {
            "symbol": "600000",
            "trade_date": "2026-05-06",
            "minute_start_ts_utc": _MINUTE_US,
            "phase": 2,
            "open": 10.0,
            "high": 11.0,
            "low": 10.0,
            "close": 11.0,
            "volume_sum": 30,
            "notional_sum": 323.0,
            "trade_count": 2,
            "tick_signed_volume_sum": 20,
            "tick_signed_notional_sum": 222.0,
        },
        {
            "symbol": "600000",
            "trade_date": "2026-05-06",
            "minute_start_ts_utc": 2 * _MINUTE_US,
            "phase": 2,
            "open": 12.0,
            "high": 12.0,
            "low": 12.0,
            "close": 12.0,
            "volume_sum": 40,
            "notional_sum": 480.0,
            "trade_count": 1,
            "tick_signed_volume_sum": 40,
            "tick_signed_notional_sum": 480.0,
        },
        {
            "symbol": "600001",
            "trade_date": "2026-05-06",
            "minute_start_ts_utc": 2 * _MINUTE_US,
            "phase": 2,
            "open": 20.0,
            "high": 20.0,
            "low": 20.0,
            "close": 20.0,
            "volume_sum": 5,
            "notional_sum": 100.0,
            "trade_count": 1,
            "tick_signed_volume_sum": -5,
            "tick_signed_notional_sum": -100.0,
        },
    ]
    assert sum(output.column("trade_count").to_pylist()) == 5
    assert sum(output.column("volume_sum").to_pylist()) == 105
    assert math.fsum(output.column("notional_sum").to_pylist()) == 1_173.0


def test_builder_uses_left_closed_minute_boundaries() -> None:
    output = build_level2_stock_trade_1m(
        _trade_table(
            symbol=["600000", "600000"],
            ts_utc=[2 * _MINUTE_US - 1, 2 * _MINUTE_US],
            main_seq=[1, 1],
            sub_seq=[1, 2],
            price=[10.0, 11.0],
            volume=[1, 1],
            security_type=["stock", "stock"],
            phase=[2, 2],
            notional=[10.0, 11.0],
            trade_side=[0, 1],
        ),
        trade_date="2026-05-06",
    )

    assert output.column("minute_start_ts_utc").to_pylist() == [
        _MINUTE_US,
        2 * _MINUTE_US,
    ]


def test_builder_preserves_exact_duplicate_ticks() -> None:
    output = build_level2_stock_trade_1m(
        _trade_table(
            symbol=["600000", "600000"],
            ts_utc=[_MINUTE_US, _MINUTE_US],
            main_seq=[1, 1],
            sub_seq=[1, 1],
            price=[10.0, 10.0],
            volume=[100, 100],
            security_type=["stock", "stock"],
            phase=[2, 2],
            notional=[1_000.0, 1_000.0],
            trade_side=[0, 0],
        ),
        trade_date="2026-05-06",
    )

    assert output.column("trade_count").to_pylist() == [2]
    assert output.column("volume_sum").to_pylist() == [200]


def test_builder_rejects_conflicting_prices_for_one_order_identity() -> None:
    with pytest.raises(ValueError, match="conflicting prices"):
        build_level2_stock_trade_1m(
            _trade_table(
                symbol=["600000", "600000"],
                ts_utc=[_MINUTE_US, _MINUTE_US],
                main_seq=[1, 1],
                sub_seq=[1, 1],
                price=[10.0, 10.1],
                volume=[100, 100],
                security_type=["stock", "stock"],
                phase=[2, 2],
                notional=[1_000.0, 1_010.0],
                trade_side=[0, 1],
            ),
            trade_date="2026-05-06",
        )


@pytest.mark.parametrize("security_types", [[], ["etf", "bond"]])
def test_builder_returns_typed_empty_output_without_stock_rows(
    security_types: list[str],
) -> None:
    row_count = len(security_types)
    output = build_level2_stock_trade_1m(
        _trade_table(
            symbol=["510050", "110000"][:row_count],
            ts_utc=[_MINUTE_US, _MINUTE_US + 1][:row_count],
            main_seq=[1, 1][:row_count],
            sub_seq=[1, 2][:row_count],
            price=[3.0, 100.0][:row_count],
            volume=[100, 10][:row_count],
            security_type=security_types,
            phase=[2, 2][:row_count],
            notional=[300.0, 1_000.0][:row_count],
            trade_side=[0, 1][:row_count],
        ),
        trade_date="2026-05-06",
    )

    assert output.num_rows == 0
    assert output.column_names == _OUTPUT_COLUMNS
    assert all(not field.nullable for field in output.schema)


@pytest.mark.parametrize(
    ("price", "notional"),
    [(float("nan"), 1_000.0), (float("inf"), 1_000.0), (10.0, float("nan"))],
)
def test_builder_rejects_non_finite_consumed_values(
    price: float,
    notional: float,
) -> None:
    with pytest.raises(ValueError, match="finite"):
        build_level2_stock_trade_1m(
            _trade_table(
                symbol=["600000"],
                ts_utc=[_MINUTE_US],
                main_seq=[1],
                sub_seq=[1],
                price=[price],
                volume=[100],
                security_type=["stock"],
                phase=[2],
                notional=[notional],
                trade_side=[0],
            ),
            trade_date="2026-05-06",
        )


def test_builder_rejects_integer_sum_overflow() -> None:
    with pytest.raises(pa.ArrowInvalid, match="Integer value out of bounds"):
        build_level2_stock_trade_1m(
            _trade_table(
                symbol=["600000", "600000"],
                ts_utc=[_MINUTE_US, _MINUTE_US + 1],
                main_seq=[1, 1],
                sub_seq=[1, 2],
                price=[10.0, 10.0],
                volume=[2**63 - 1, 1],
                security_type=["stock", "stock"],
                phase=[2, 2],
                notional=[1.0, 1.0],
                trade_side=[0, 0],
            ),
            trade_date="2026-05-06",
        )


def test_builder_rejects_float_sum_overflow() -> None:
    with pytest.raises(ValueError, match="finite"):
        build_level2_stock_trade_1m(
            _trade_table(
                symbol=["600000", "600000"],
                ts_utc=[_MINUTE_US, _MINUTE_US + 1],
                main_seq=[1, 1],
                sub_seq=[1, 2],
                price=[10.0, 10.0],
                volume=[1, 1],
                security_type=["stock", "stock"],
                phase=[2, 2],
                notional=[1.7e308, 1.7e308],
                trade_side=[0, 0],
            ),
            trade_date="2026-05-06",
        )


def _trade_table(
    *,
    symbol: list[str],
    ts_utc: list[int],
    main_seq: list[int],
    sub_seq: list[int],
    price: list[float],
    volume: list[int],
    security_type: list[str],
    phase: list[int],
    notional: list[float],
    trade_side: list[int],
) -> pa.Table:
    return pa.table(
        {
            "symbol": pa.array(symbol, type=pa.string()),
            "ts_utc": pa.array(ts_utc, type=pa.int64()),
            "main_seq": pa.array(main_seq, type=pa.int64()),
            "sub_seq": pa.array(sub_seq, type=pa.int64()),
            "price": pa.array(price, type=pa.float64()),
            "volume": pa.array(volume, type=pa.int64()),
            "security_type": pa.array(security_type, type=pa.string()),
            "phase": pa.array(phase, type=pa.int8()),
            "notional": pa.array(notional, type=pa.float64()),
            "trade_side": pa.array(trade_side, type=pa.int8()),
        }
    )
