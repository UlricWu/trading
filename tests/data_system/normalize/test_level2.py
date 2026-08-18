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
        ("main_seq", pa.int64()),
        ("sub_seq", pa.int64()),
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
        time_field="TickTime",
        event_field="TickType",
        event_mapping={"T": "TRADE"},
        price_field="Price",
        volume_field="Volume",
        side_field="Side",
        side_mapping={"1": "B", "2": "S"},
        buy_no_field="BuyNo",
        sell_no_field="SellNo",
    )


def _sz_spec() -> Level2TradeSpec:
    return Level2TradeSpec(
        raw_object="SZ_Trade",
        output="sz_trade",
        exchange="sz",
        symbol_field="SecurityID",
        time_field="TickTime",
        event_field="ExecType",
        event_mapping={"1": "TRADE", "2": "CANCEL"},
        price_field="TradePrice",
        volume_field="TradeVolume",
        side_field=None,
        side_mapping=None,
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


def test_normalize_level2_rejects_invalid_partition_date_before_input_read(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="trade_date must be a valid calendar date"):
        normalize_level2(
            input_file=tmp_path / "missing.csv.7z",
            output_name=tmp_path / "sh_trade.parquet",
            raw_object="SH_Stock_OrderTrade",
            trade_date="2026-02-30",
            target_name="sh_trade",
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
            "TickTime": ["9150004", "9150004", "9150004", "9150005", "9150005"],
            "TickType": ["T", "T", "T", "T", "T"],
            "Price": ["1.0", "1.0", "1.0", "1.0", "1.0"],
            "Volume": ["100", "100", "100", "124800", "3000"],
            "Side": ["1", "1", "1", "2", "2"],
            "MainSeq": ["1", "1", "1", "1", "1"],
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
            "TickTime": ["60000150", "60000150"],
            "TradePrice": ["1.0", "2.0"],
            "TradeVolume": ["100", "200"],
            "ExecType": ["1", "1"],
            "MainSeq": ["2011", "2011"],
            "SubSeq": ["1", "2"],
            "BuyNo": ["0", "0"],
            "SellNo": ["0", "0"],
        }
    )


def test_parse_empty_table_returns_internal_schema() -> None:
    output = parse_level2_trade_batch(
        pa.table({}),
        spec=_sh_spec(),
        trade_date="2025-11-10",
    )

    assert output.num_rows == 0
    assert output.schema == _EXPECTED_SCHEMA


def test_parse_sh_trade_produces_utc_epoch_microseconds(
    sh_trade_table: pa.Table,
) -> None:
    output = parse_level2_trade_batch(
        sh_trade_table,
        spec=_sh_spec(),
        trade_date="2025-11-10",
    )

    assert output.schema == _EXPECTED_SCHEMA
    assert output["ts_utc"].to_pylist() == [
        1762737300040000,
        1762737300040000,
        1762737300040000,
        1762737300050000,
        1762737300050000,
    ]
    assert output["main_seq"].to_pylist() == [1, 1, 1, 1, 1]
    assert output["sub_seq"].to_pylist() == [539, 2433, 2538, 2842, 2431]
    assert output["order_id"].to_pylist() == output["sub_seq"].to_pylist()


def test_parse_sz_trade_produces_utc_epoch_microseconds(
    sz_trade_table: pa.Table,
) -> None:
    output = parse_level2_trade_batch(
        sz_trade_table,
        spec=_sz_spec(),
        trade_date="2025-11-03",
    )

    assert output.schema == _EXPECTED_SCHEMA
    assert output["ts_utc"].to_pylist() == [
        1762120800150000,
        1762120800150000,
    ]
    assert output["side"].null_count == 2


@pytest.mark.parametrize(
    ("tick_time", "local_time"),
    [
        ("0", time(0, 0)),
        ("9250002", time(9, 25, 0, 20_000)),
        ("15150171", time(15, 15, 1, 710_000)),
    ],
)
def test_parse_sh_centisecond_tick_time_mapping(
    sh_trade_table: pa.Table,
    tick_time: str,
    local_time: time,
) -> None:
    table = sh_trade_table.set_column(
        sh_trade_table.column_names.index("TickTime"),
        "TickTime",
        pa.array([tick_time] * sh_trade_table.num_rows, type=pa.string()),
    )

    output = parse_level2_trade_batch(
        table,
        spec=_sh_spec(),
        trade_date="2025-11-10",
    )

    expected = DateTimeUtils.local_time_to_utc_epoch_us(
        local_time,
        date(2025, 11, 10),
    )
    assert output["ts_utc"].to_pylist() == [expected] * table.num_rows


def test_parse_uses_tick_time_and_ignores_trade_time_suffix(
    sh_trade_table: pa.Table,
) -> None:
    table = sh_trade_table.set_column(
        sh_trade_table.column_names.index("TradeTime"),
        "TradeTime",
        pa.array(
            ["2025-11-10 not-a-clock"] * sh_trade_table.num_rows,
            type=pa.string(),
        ),
    )

    output = parse_level2_trade_batch(
        table,
        spec=_sh_spec(),
        trade_date="2025-11-10",
    )

    assert output["ts_utc"].to_pylist() == [
        1762737300040000,
        1762737300040000,
        1762737300040000,
        1762737300050000,
        1762737300050000,
    ]


def test_parse_requires_tick_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.drop_columns(["TickTime"])

    with pytest.raises(
        ValueError,
        match=r"columns must exist exactly once: \['TickTime'\]",
    ):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


def test_parse_sh_trade_preserves_defined_field_mappings(
    sh_trade_table: pa.Table,
) -> None:
    output = parse_level2_trade_batch(
        sh_trade_table,
        spec=_sh_spec(),
        trade_date="2025-11-10",
    )

    assert output["event"].to_pylist() == ["TRADE"] * 5
    assert output["side"].to_pylist() == ["B", "B", "B", "S", "S"]
    assert output["buy_no"].to_pylist() == [0, 2432, 356, 0, 0]
    assert output["sell_no"].to_pylist() == [76, 0, 0, 2841, 2429]


def test_parse_preserves_observed_sequence_without_completeness_validation(
    sz_trade_table: pa.Table,
) -> None:
    table = sz_trade_table.set_column(
        sz_trade_table.column_names.index("SubSeq"),
        "SubSeq",
        pa.array([9, 11], type=pa.int64()),
    )

    output = parse_level2_trade_batch(
        table,
        spec=_sz_spec(),
        trade_date="2025-11-03",
    )

    assert output["main_seq"].to_pylist() == [2011, 2011]
    assert output["sub_seq"].to_pylist() == [9, 11]


@pytest.mark.parametrize("field", ["MainSeq", "SubSeq"])
def test_parse_requires_broker_sequence_fields(
    sh_trade_table: pa.Table,
    field: str,
) -> None:
    table = sh_trade_table.drop_columns([field])

    with pytest.raises(
        ValueError,
        match=rf"columns must exist exactly once: \['{field}'\]",
    ):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


def test_parse_keeps_only_positive_trade_rows() -> None:
    table = pa.table(
        {
            "TradeTime": ["2025-11-03 09:30:00.1"] * 4,
            "SecurityID": ["000001", "000002", "000003", "000004"],
            "TickTime": ["93000000", "93000001", "93000002", "93000003"],
            "TradePrice": ["10.0", "10.0", "0.0", "10.0"],
            "TradeVolume": ["100", "100", "100", "0"],
            "ExecType": ["1", "2", "1", "1"],
            "MainSeq": ["2011", "2011", "2011", "2011"],
            "SubSeq": ["1", "2", "3", "4"],
            "BuyNo": ["1", "2", "3", "4"],
            "SellNo": ["5", "6", "7", "8"],
        }
    )

    output = parse_level2_trade_batch(
        table,
        spec=_sz_spec(),
        trade_date="2025-11-03",
    )

    assert output["symbol"].to_pylist() == ["000001"]
    assert output["event"].to_pylist() == ["TRADE"]
    assert output["main_seq"].to_pylist() == [2011]
    assert output["sub_seq"].to_pylist() == [1]


@pytest.mark.parametrize(
    "trade_time",
    [
        "2025-11-09 09:15:00.04",
        "2025-11",
        "not-a-date",
    ],
)
def test_parse_rejects_trade_time_date_mismatch(
    sh_trade_table: pa.Table,
    trade_time: str,
) -> None:
    table = sh_trade_table.set_column(
        0,
        "TradeTime",
        pa.array([trade_time] * sh_trade_table.num_rows, type=pa.string()),
    )

    with pytest.raises(ValueError, match="TradeTime date must match trade_date"):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


def test_parse_rejects_null_trade_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.set_column(
        0,
        "TradeTime",
        pa.array([None] * sh_trade_table.num_rows, type=pa.string()),
    )

    with pytest.raises(ValueError, match="must not contain null"):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


def test_parse_rejects_non_string_trade_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.set_column(
        0,
        "TradeTime",
        pa.array([1] * sh_trade_table.num_rows, type=pa.int64()),
    )

    with pytest.raises(TypeError, match="must be a string column"):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


def test_parse_requires_trade_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.drop_columns(["TradeTime"])

    with pytest.raises(
        ValueError,
        match=r"columns must exist exactly once: \['TradeTime'\]",
    ):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


@pytest.mark.parametrize("tick_time", ["", "091500020", "09:15:00", "-1"])
def test_parse_rejects_invalid_sh_tick_time_encoding(
    sh_trade_table: pa.Table,
    tick_time: str,
) -> None:
    table = sh_trade_table.set_column(
        sh_trade_table.column_names.index("TickTime"),
        "TickTime",
        pa.array([tick_time] * sh_trade_table.num_rows, type=pa.string()),
    )

    with pytest.raises(ValueError, match="SH TickTime must contain 1-8 digits"):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


@pytest.mark.parametrize("tick_time", ["24000000", "12600000", "12596000"])
def test_parse_rejects_invalid_sh_tick_time_clock(
    sh_trade_table: pa.Table,
    tick_time: str,
) -> None:
    table = sh_trade_table.set_column(
        sh_trade_table.column_names.index("TickTime"),
        "TickTime",
        pa.array([tick_time] * sh_trade_table.num_rows, type=pa.string()),
    )

    with pytest.raises(ValueError, match="invalid wall-clock time"):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


def test_parse_rejects_null_tick_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.set_column(
        sh_trade_table.column_names.index("TickTime"),
        "TickTime",
        pa.array([None] * sh_trade_table.num_rows, type=pa.string()),
    )

    with pytest.raises(ValueError, match="SH TickTime must not contain null"):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


def test_parse_rejects_non_string_tick_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.set_column(
        sh_trade_table.column_names.index("TickTime"),
        "TickTime",
        pa.array([9150004] * sh_trade_table.num_rows, type=pa.int64()),
    )

    with pytest.raises(TypeError, match="SH TickTime must be a string column"):
        parse_level2_trade_batch(
            table,
            spec=_sh_spec(),
            trade_date="2025-11-10",
        )


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
            "event": ["TRADE", "TRADE", "TRADE"],
            "order_id": [10, 20, 30],
            "main_seq": [1, 2, 1],
            "sub_seq": [10, 20, 30],
            "side": pa.array([None, None, None], type=pa.string()),
            "price": [10.5, 20.0, 10.0],
            "volume": [100, 200, 300],
            "buy_no": [1, 2, 3],
            "sell_no": [4, 5, 6],
            "security_type": ["stock", "stock", "stock"],
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
    assert output.table.column_names == [
        "symbol",
        "ts_utc",
        "event",
        "order_id",
        "main_seq",
        "sub_seq",
        "side",
        "price",
        "volume",
        "buy_no",
        "sell_no",
        "security_type",
        "phase",
        "notional",
        "trade_side",
    ]
    assert output.symbol_slices == {
        "600000": range(2),
        "600001": range(2, 3),
    }


def test_build_processed_trade_day_uses_broker_sequence_for_timestamp_ties() -> None:
    trade_day = date(2026, 7, 14)
    timestamp = DateTimeUtils.local_time_to_utc_epoch_us(
        time(9, 30),
        trade_day,
    )
    table = pa.table(
        {
            "symbol": ["600000", "600000", "600000"],
            "ts_utc": pa.array([timestamp, timestamp, timestamp], type=pa.int64()),
            "event": ["TRADE", "TRADE", "TRADE"],
            "order_id": [30, 20, 10],
            "main_seq": [2, 1, 1],
            "sub_seq": [30, 20, 10],
            "side": pa.array([None, None, None], type=pa.string()),
            "price": [10.5, 10.0, 9.5],
            "volume": [100, 100, 100],
            "buy_no": [3, 1, 2],
            "sell_no": [6, 4, 5],
            "security_type": ["stock", "stock", "stock"],
        }
    )

    output = build_processed_level2_trade_day(
        table,
        exchange="sh",
        trade_date=trade_day.isoformat(),
    )

    assert list(
        zip(
            output.table["main_seq"].to_pylist(),
            output.table["sub_seq"].to_pylist(),
            strict=True,
        )
    ) == [(1, 10), (1, 20), (2, 30)]
    assert output.table["trade_side"].to_pylist() == [0, 1, 1]


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
            "main_seq": [1],
            "sub_seq": [1],
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
