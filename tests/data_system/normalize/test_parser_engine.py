# filepath: tests/data_system/normalize/test_parser_engine.py
from __future__ import annotations

import pyarrow as pa
import pytest

from src.data_system.normalize.parser_engine import INTERNAL_SCHEMA, parse_events_arrow


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
            "Price": ["0.0", "0.0", "0.0", "0.0", "0.0"],
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
            "TradePrice": ["0.0", "0.0"],
            "TradeVolume": ["100", "200"],
            "ExecType": ["2", "2"],
            "SubSeq": ["1", "2"],
            "BuyNo": ["0", "0"],
            "SellNo": ["0", "0"],
        }
    )


def test_parse_empty_table_returns_internal_schema() -> None:
    output = parse_events_arrow(pa.table({}), exchange="sh", kind="trade")

    assert output.num_rows == 0
    assert output.schema == INTERNAL_SCHEMA


@pytest.mark.parametrize(
    ("exchange", "kind"),
    [
        ("xx", "trade"),
        ("sh", "quote"),
    ],
)
def test_parse_rejects_unsupported_route_before_empty_return(
    exchange: str,
    kind: str,
) -> None:
    with pytest.raises(ValueError, match="unsupported Level-2 parser route"):
        parse_events_arrow(pa.table({}), exchange=exchange, kind=kind)


def test_parse_sh_trade_produces_utc_epoch_microseconds(
    sh_trade_table: pa.Table,
) -> None:
    output = parse_events_arrow(sh_trade_table, exchange="sh", kind="trade")

    assert output.schema == INTERNAL_SCHEMA
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
    output = parse_events_arrow(sz_trade_table, exchange="sz", kind="trade")

    assert output.schema == INTERNAL_SCHEMA
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

    output = parse_events_arrow(table, exchange="sh", kind="trade")

    assert output["ts_utc"].to_pylist() == [expected] * table.num_rows


def test_parse_does_not_use_tick_time(sh_trade_table: pa.Table) -> None:
    without_tick_time = sh_trade_table.drop_columns(["TickTime"])

    output = parse_events_arrow(without_tick_time, exchange="sh", kind="trade")

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
    output = parse_events_arrow(sh_trade_table, exchange="sh", kind="trade")

    assert output["event"].to_pylist() == ["TRADE"] * 5
    assert output["side"].to_pylist() == ["B", "B", "B", "S", "S"]
    assert output["buy_no"].to_pylist() == [0, 2432, 356, 0, 0]
    assert output["sell_no"].to_pylist() == [76, 0, 0, 2841, 2429]


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
        parse_events_arrow(table, exchange="sh", kind="trade")


def test_parse_rejects_null_trade_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.set_column(
        0,
        "TradeTime",
        pa.array([None] * sh_trade_table.num_rows, type=pa.string()),
    )

    with pytest.raises(ValueError, match="must not contain null"):
        parse_events_arrow(table, exchange="sh", kind="trade")


def test_parse_rejects_non_string_trade_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.set_column(
        0,
        "TradeTime",
        pa.array([1] * sh_trade_table.num_rows, type=pa.int64()),
    )

    with pytest.raises(TypeError, match="must be a string column"):
        parse_events_arrow(table, exchange="sh", kind="trade")


def test_parse_requires_trade_time(sh_trade_table: pa.Table) -> None:
    table = sh_trade_table.drop_columns(["TradeTime"])

    with pytest.raises(ValueError, match="TradeTime is required"):
        parse_events_arrow(table, exchange="sh", kind="trade")
