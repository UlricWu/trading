# filepath: tests/data_system/normalize/test_level2.py
"""Behavior tests for admitted Level-2 trade normalization."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pyarrow as pa
import pytest

from src.data_system.market_phase import MarketPhase
from src.data_system.normalize.level2 import (
    Level2TradeSpec,
    build_processed_level2_trade_day,
    normalize_level2,
    parse_level2_trade_batch,
)
from src.utils.datetime_utils import DateTimeUtils

_EXPECTED_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("ts_utc", pa.int64()),
        ("event", pa.string()),
        ("order_id", pa.int64()),
        ("side", pa.string()),
        ("price", pa.float64()),
        ("volume", pa.int64()),
        ("buy_no", pa.int64()),
        ("sell_no", pa.int64()),
    ]
)


def _sh_spec() -> Level2TradeSpec:
    return Level2TradeSpec(
        raw_object="SH_Stock_OrderTrade",
        output="sh_trade",
        exchange="sh",
        symbol_field="SecurityID",
        time_field="TradeTime",
        event_field="TickType",
        event_mapping={"T": "TRADE"},
        price_field="Price",
        volume_field="Volume",
        side_field="Side",
        side_mapping={"1": "B", "2": "S"},
        id_field="SubSeq",
        buy_no_field="BuyNo",
        sell_no_field="SellNo",
    )


def _sz_spec() -> Level2TradeSpec:
    return Level2TradeSpec(
        raw_object="SZ_Trade",
        output="sz_trade",
        exchange="sz",
        symbol_field="SecurityID",
        time_field="TradeTime",
        event_field="ExecType",
        event_mapping={"1": "TRADE", "2": "CANCEL"},
        price_field="TradePrice",
        volume_field="TradeVolume",
        side_field=None,
        side_mapping=None,
        id_field="SubSeq",
        buy_no_field="BuyNo",
        sell_no_field="SellNo",
    )


def test_normalize_level2_rejects_cross_exchange_route_before_input_read(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unsupported Level-2 raw/output"):
        normalize_level2(
            input_file=tmp_path / "missing.csv.7z",
            output_name=tmp_path / "sz_trade.parquet",
            raw_object="SH_Stock_OrderTrade",
            trade_date="2026-07-27",
            target_name="sz_trade",
        )


@pytest.fixture
def sh_trade_table() -> pa.Table:
    return pa.table(
        {
            "TradeTime": [
                "2025-11-10 09:15:00.04",
                "2025-11-10 09:15:00.04",
                "2025-11-10 09:15:00.04",
                "2025-11-10 09:15:00.05",
                "2025-11-10 09:15:00.05",
            ],
            "SecurityID": ["803", "2936", "2936", "609", "300007"],
            "TickTime": ["91500040", "91500040", "1", "91500050", "999999999"],
            "TickType": ["T", "T", "T", "T", "T"],
            "Price": ["1.0", "1.0", "1.0", "1.0", "1.0"],
            "Volume": ["100", "100", "100", "124800", "3000"],
            "Side": ["1", "1", "1", "2", "2"],
            "SubSeq": ["539", "2433", "2538", "2842", "2431"],
            "BuyNo": ["0", "2432", "356", "0", "0"],
            "SellNo": ["76", "0", "0", "2841", "2429"],
        }
    )


@pytest.fixture
def sz_trade_table() -> pa.Table:
    return pa.table(
        {
            "TradeTime": [
                "2025-11-03 06:00:00.15",
                "2025-11-03 06:00:00.15",
            ],
            "SecurityID": ["751028", "751900"],
            "TickTime": ["6000015", "6000015"],
            "TradePrice": ["1.0", "2.0"],
            "TradeVolume": ["100", "200"],
            "ExecType": ["1", "1"],
            "SubSeq": ["1", "2"],
            "BuyNo": ["0", "0"],
            "SellNo": ["0", "0"],
        }
    )


def test_parse_empty_table_returns_internal_schema() -> None:
    output = parse_level2_trade_batch(pa.table({}), spec=_sh_spec())

    assert output.num_rows == 0
    assert output.schema == _EXPECTED_SCHEMA


def test_parse_sh_trade_produces_utc_epoch_microseconds(
    sh_trade_table: pa.Table,
) -> None:
    output = parse_level2_trade_batch(sh_trade_table, spec=_sh_spec())

    assert output.schema == _EXPECTED_SCHEMA
    assert output["ts_utc"].to_pylist() == [
        1762737300040000,
        1762737300040000,
        1762737300040000,
        1762737300050000,
        1762737300050000,
    ]


def test_parse_sz_trade_produces_utc_epoch_microseconds(
    sz_trade_table: pa.Table,
) -> None:
    output = parse_level2_trade_batch(sz_trade_table, spec=_sz_spec())

    assert output.schema == _EXPECTED_SCHEMA
    assert output["ts_utc"].to_pylist() == [
        1762120800150000,
        1762120800150000,
    ]
    assert output["side"].null_count == 2


@pytest.mark.parametrize(
    ("trade_time", "expected"),
    [
        ("2025-11-10 09:15:00.0", 1762737300000000),
        ("2025-11-10 09:15:00.04", 1762737300040000),
        ("2025-11-10 09:15:00.123456", 1762737300123456),
    ],
)
def test_parse_right_pads_fractional_seconds_to_microseconds(
    sh_trade_table: pa.Table,
    trade_time: str,
    expected: int,
) -> None:
    table = sh_trade_table.set_column(
        0,
        "TradeTime",
        pa.array([trade_time] * sh_trade_table.num_rows, type=pa.string()),
    )

    output = parse_level2_trade_batch(table, spec=_sh_spec())

    assert output["ts_utc"].to_pylist() == [expected] * table.num_rows


def test_parse_does_not_use_tick_time(sh_trade_table: pa.Table) -> None:
    without_tick_time = sh_trade_table.drop_columns(["TickTime"])

    output = parse_level2_trade_batch(without_tick_time, spec=_sh_spec())

    assert output["ts_utc"].to_pylist() == [
        1762737300040000,
        1762737300040000,
        1762737300040000,
        1762737300050000,
        1762737300050000,
    ]


def test_parse_sh_trade_preserves_defined_field_mappings(
    sh_trade_table: pa.Table,
) -> None:
    output = parse_level2_trade_batch(sh_trade_table, spec=_sh_spec())

    assert output["event"].to_pylist() == ["TRADE"] * 5
    assert output["side"].to_pylist() == ["B", "B", "B", "S", "S"]
    assert output["buy_no"].to_pylist() == [0, 2432, 356, 0, 0]
    assert output["sell_no"].to_pylist() == [76, 0, 0, 2841, 2429]


def test_parse_keeps_only_positive_trade_rows() -> None:
    table = pa.table(
        {
            "TradeTime": ["2025-11-03 09:30:00.1"] * 4,
            "SecurityID": ["000001", "000002", "000003", "000004"],
            "TradePrice": ["10.0", "10.0", "0.0", "10.0"],
            "TradeVolume": ["100", "100", "100", "0"],
            "ExecType": ["1", "2", "1", "1"],
            "SubSeq": ["1", "2", "3", "4"],
            "BuyNo": ["1", "2", "3", "4"],
            "SellNo": ["5", "6", "7", "8"],
        }
    )

    output = parse_level2_trade_batch(table, spec=_sz_spec())

    assert output["symbol"].to_pylist() == ["000001"]
    assert output["event"].to_pylist() == ["TRADE"]


@pytest.mark.parametrize(
    "trade_time",
    [
        "2025-11-10 09:15:00",
        "2025-11-10 09:15:00.1234567",
        " 2025-11-10 09:15:00.04",
        "2025-11-10 09:15:00.04 ",
        "2025-02-30 09:15:00.04",
        "2025-11-10 25:15:00.04",
    ],
)
def test_parse_rejects_invalid_trade_time_values(
    sh_trade_table: pa.Table,
    trade_time: str,
) -> None:
    table = sh_trade_table.set_column(
        0,
        "TradeTime",
        pa.array([trade_time] * sh_trade_table.num_rows, type=pa.string()),
    )

    with pytest.raises(ValueError):
        parse_level2_trade_batch(table, spec=_sh_spec())


def test_parse_rejects_null_trade_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.set_column(
        0,
        "TradeTime",
        pa.array([None] * sh_trade_table.num_rows, type=pa.string()),
    )

    with pytest.raises(ValueError, match="must not contain null"):
        parse_level2_trade_batch(table, spec=_sh_spec())


def test_parse_rejects_non_string_trade_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.set_column(
        0,
        "TradeTime",
        pa.array([1] * sh_trade_table.num_rows, type=pa.int64()),
    )

    with pytest.raises(TypeError, match="must be a string column"):
        parse_level2_trade_batch(table, spec=_sh_spec())


def test_parse_requires_trade_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.drop_columns(["TradeTime"])

    with pytest.raises(
        ValueError,
        match=r"columns must exist exactly once: \['TradeTime'\]",
    ):
        parse_level2_trade_batch(table, spec=_sh_spec())


def test_build_processed_trade_day_sorts_indexes_and_enriches_symbols() -> None:
    trade_day = date(2026, 7, 14)
    table = pa.table(
        {
            "symbol": ["600000", "600001", "600000"],
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(9, 30, 2),
                        trade_day,
                    ),
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(9, 30),
                        trade_day,
                    ),
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(9, 30, 1),
                        trade_day,
                    ),
                ],
                type=pa.int64(),
            ),
            "security_type": ["stock", "stock", "stock"],
            "price": [10.5, 20.0, 10.0],
            "volume": [100, 200, 300],
        }
    )

    output = build_processed_level2_trade_day(
        table,
        exchange="sh",
        trade_date=trade_day.isoformat(),
    )

    assert output.table["symbol"].to_pylist() == [
        "600000",
        "600000",
        "600001",
    ]
    assert output.table["phase"].to_pylist() == [
        int(MarketPhase.CONTINUOUS),
        int(MarketPhase.CONTINUOUS),
        int(MarketPhase.CONTINUOUS),
    ]
    assert output.table["notional"].to_pylist() == [3000.0, 1050.0, 4000.0]
    assert output.table["trade_side"].to_pylist() == [0, 1, 0]
    assert output.symbol_slices == {
        "600000": range(2),
        "600001": range(2, 3),
    }


@pytest.mark.parametrize("symbol", ["", None])
def test_build_processed_trade_day_rejects_missing_symbol_identity(
    symbol: str | None,
) -> None:
    trade_day = date(2026, 7, 14)
    table = pa.table(
        {
            "symbol": pa.array([symbol], type=pa.string()),
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(9, 30),
                        trade_day,
                    )
                ],
                type=pa.int64(),
            ),
            "security_type": ["stock"],
            "price": [10.0],
            "volume": [100],
        }
    )

    with pytest.raises(ValueError, match="non-empty string"):
        build_processed_level2_trade_day(
            table,
            exchange="sh",
            trade_date=trade_day.isoformat(),
        )
