# filepath: tests/data_system/builders/test_stock_1430.py
"""Behavior tests for the fixed V1 14:30 Feature and Label builders."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import TypedDict
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pytest

from src.data_system.builders.stock_1430 import (
    build_stock_1430_features,
    build_stock_1430_labels,
)
from src.data_system.market_phase import MarketPhase
from src.utils.datetime_utils import DateTimeUtils

_MINUTE_SCHEMA = pa.schema(
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


def test_feature_builder_applies_v1_windows_ranks_and_sparse_nulls() -> None:
    trade_date = "2026-05-06"
    minutes = _minute_table(
        trade_date,
        [
            _row(
                "000001",
                time(14, 25),
                high=10.0,
                low=10.0,
                volume=100,
                notional=1_000.0,
                trades=2,
                signed_volume=50,
                signed_notional=500.0,
            ),
            _row(
                "000001",
                time(14, 29),
                high=12.0,
                low=11.0,
                volume=100,
                notional=1_100.0,
                trades=4,
                signed_volume=-20,
                signed_notional=-220.0,
            ),
            _row(
                "600000",
                time(14, 25),
                high=20.0,
                low=20.0,
                volume=100,
                notional=2_000.0,
                trades=3,
                signed_volume=0,
                signed_notional=0.0,
            ),
            _row(
                "600000",
                time(14, 29),
                high=21.0,
                low=20.0,
                volume=100,
                notional=2_000.0,
                trades=3,
                signed_volume=100,
                signed_notional=2_000.0,
            ),
            _row(
                "300001",
                time(13, 0),
                high=30.0,
                low=30.0,
                volume=100,
                notional=3_000.0,
                trades=1,
                signed_volume=0,
                signed_notional=0.0,
            ),
            _row("900001", time(14, 20), phase=MarketPhase.AUCTION),
            _row("900002", time(14, 30)),
        ],
    )

    output = build_stock_1430_features(
        minutes, trade_date=date.fromisoformat(trade_date)
    )
    frame = output.to_pandas().set_index("symbol")

    assert output.num_columns == 35
    assert output.column_names[:3] == [
        "symbol",
        "trade_date",
        "decision_ts_utc",
    ]
    assert output.column("symbol").to_pylist() == ["000001", "300001", "600000"]
    assert (
        output.column("decision_ts_utc").to_pylist()
        == [int(datetime(2026, 5, 6, 6, 30, tzinfo=UTC).timestamp() * 1_000_000)] * 3
    )
    assert frame.loc["000001", "f_l2_edge_vwap_return_rank_5m"] == 1.0
    assert frame.loc["600000", "f_l2_edge_vwap_return_rank_5m"] == 0.5
    assert frame.loc["000001", "f_l2_high_low_range_rank_5m"] == 1.0
    assert frame.loc["600000", "f_l2_high_low_range_rank_5m"] == 0.5
    assert frame.loc["000001", "f_l2_notional_rank_5m"] == 0.5
    assert frame.loc["600000", "f_l2_notional_rank_5m"] == 1.0
    assert frame.loc["000001", "f_l2_trade_count_rank_5m"] == 0.75
    assert frame.loc["600000", "f_l2_trade_count_rank_5m"] == 0.75
    assert frame.loc["000001", "f_l2_average_trade_notional_rank_5m"] == 0.5
    assert frame.loc["600000", "f_l2_average_trade_notional_rank_5m"] == 1.0
    assert frame.loc["000001", "f_l2_tick_signed_volume_ratio_rank_5m"] == 0.5
    assert frame.loc["600000", "f_l2_tick_signed_volume_ratio_rank_5m"] == 1.0
    assert frame.loc["000001", "f_l2_tick_signed_notional_ratio_rank_5m"] == 0.5
    assert frame.loc["600000", "f_l2_tick_signed_notional_ratio_rank_5m"] == 1.0
    assert frame.loc["000001", "f_l2_observed_minute_ratio_5m"] == 0.4
    assert frame.loc["600000", "f_l2_observed_minute_ratio_5m"] == 0.4
    assert frame.loc["300001", "f_l2_observed_minute_ratio_5m"] == 0.0
    assert pd.isna(frame.loc["300001", "f_l2_notional_rank_5m"])
    assert pd.isna(frame.loc["000001", "f_l2_edge_vwap_return_rank_15m"])
    assert frame.loc["000001", "f_l2_observed_minute_ratio_60m"] == pytest.approx(
        2 / 60
    )


@pytest.mark.parametrize("signed_direction", (-1, 1))
def test_feature_builder_sums_large_minute_volumes_without_overflow(
    signed_direction: int,
) -> None:
    trade_date = "2026-05-06"
    volume = 2**62
    minutes = _minute_table(
        trade_date,
        [
            _row(
                "000001",
                minute,
                volume=volume,
                notional=float(volume) * 10.0,
                signed_volume=signed_direction * volume,
                signed_notional=signed_direction * float(volume) * 10.0,
            )
            for minute in (time(14, 25), time(14, 27), time(14, 29))
        ],
    )

    output = build_stock_1430_features(
        minutes, trade_date=date.fromisoformat(trade_date)
    )

    for window in (5, 15, 30, 60):
        assert output.column(
            f"f_l2_tick_signed_volume_ratio_rank_{window}m"
        ).to_pylist() == [1.0]
        assert output.column(
            f"f_l2_tick_signed_notional_ratio_rank_{window}m"
        ).to_pylist() == [1.0]


def test_feature_builder_ranks_large_trade_counts_without_float_ties() -> None:
    trade_date = "2026-05-06"
    minutes = _minute_table(
        trade_date,
        [
            _row("000001", time(14, 25), trades=2**62),
            _row("000001", time(14, 29), trades=2**62),
            _row("300001", time(10, 0)),
            _row("600000", time(14, 25), trades=2**62),
            _row("600000", time(14, 29), trades=2**62 - 1),
        ],
    )

    output = build_stock_1430_features(
        minutes, trade_date=date.fromisoformat(trade_date)
    )

    for window in (5, 15, 30, 60):
        assert output.column(f"f_l2_trade_count_rank_{window}m").to_pylist() == [
            1.0,
            None,
            0.5,
        ]


@pytest.mark.parametrize("large_side", ("entry", "exit"))
def test_label_builder_sums_large_minute_volumes_without_overflow(
    large_side: str,
) -> None:
    trade_date = "2026-05-06"
    next_trade_date = "2026-05-07"
    symbols = ("000001",)
    volume = 2**62
    large_rows = [
        _row("000001", minute, volume=volume, notional=float(volume) * 10.0)
        for minute in (time(14, 31), time(14, 35))
    ]
    ordinary_rows = [_row("000001", time(14, 31))]

    output = build_stock_1430_labels(
        feature_keys=_feature_keys(trade_date, symbols),
        entry_minutes=_minute_table(
            trade_date, large_rows if large_side == "entry" else ordinary_rows
        ),
        exit_minutes=_minute_table(
            next_trade_date, large_rows if large_side == "exit" else ordinary_rows
        ),
        entry_factors=_factor_frame(trade_date, symbols),
        exit_factors=_factor_frame(next_trade_date, symbols),
        trade_date=date.fromisoformat(trade_date),
        next_trade_date=date.fromisoformat(next_trade_date),
    )

    assert output.column("y_rank_return").to_pylist() == [1.0]


def test_feature_builder_ignores_decision_and_later_rows() -> None:
    trade_date = "2026-05-06"
    visible = _row("000001", time(14, 29), notional=1_000.0, volume=100)
    first = _minute_table(
        trade_date,
        [visible, _row("000001", time(14, 30), notional=2_000.0, volume=100)],
    )
    second = _minute_table(
        trade_date,
        [visible, _row("600000", time(14, 31), notional=99_000.0, volume=1)],
    )

    assert build_stock_1430_features(
        first, trade_date=date.fromisoformat(trade_date)
    ).equals(
        build_stock_1430_features(second, trade_date=date.fromisoformat(trade_date))
    )


def test_feature_builder_returns_fixed_schema_for_empty_universe() -> None:
    output = build_stock_1430_features(
        pa.Table.from_batches([], schema=_MINUTE_SCHEMA),
        trade_date=date.fromisoformat("2026-05-06"),
    )

    assert output.num_rows == 0
    assert output.num_columns == 35
    assert all(
        not output.schema.field(column).nullable
        for column in (
            "symbol",
            "trade_date",
            "decision_ts_utc",
            "f_l2_observed_minute_ratio_5m",
            "f_l2_observed_minute_ratio_15m",
            "f_l2_observed_minute_ratio_30m",
            "f_l2_observed_minute_ratio_60m",
        )
    )


def test_label_builder_uses_observed_window_vwap_and_feature_row_set() -> None:
    trade_date = "2026-05-06"
    next_trade_date = "2026-05-07"
    features = _feature_keys(trade_date, ("000001", "300001", "600000"))
    entry = _minute_table(
        trade_date,
        [
            _row("000001", time(14, 31), notional=1_000.0, volume=100),
            _row("000001", time(14, 35), notional=1_200.0, volume=100),
            _row("300001", time(14, 32), notional=1_000.0, volume=100),
            _row("600000", time(14, 32), notional=2_000.0, volume=100),
        ],
    )
    exit_minutes = _minute_table(
        next_trade_date,
        [
            _row("000001", time(14, 31), notional=1_200.0, volume=100),
            _row("300001", time(14, 31), notional=1_100.0, volume=100),
            _row("600000", time(14, 31), notional=1_800.0, volume=100),
        ],
    )
    entry_factors = pd.DataFrame(
        {
            "symbol": ["000001", "300001", "600000"],
            "trade_date": [trade_date] * 3,
            "adj_factor": [2.0, 1.0, 1.0],
        }
    )
    exit_factors = pd.DataFrame(
        {
            "symbol": ["000001", "600000"],
            "trade_date": [next_trade_date] * 2,
            "adj_factor": [2.0, 1.0],
        }
    )

    output = build_stock_1430_labels(
        feature_keys=features,
        entry_minutes=entry,
        exit_minutes=exit_minutes,
        entry_factors=entry_factors,
        exit_factors=exit_factors,
        trade_date=date.fromisoformat(trade_date),
        next_trade_date=date.fromisoformat(next_trade_date),
    )

    assert output.select(["symbol", "trade_date", "decision_ts_utc"]).equals(features)
    assert output.column("y_rank_return").to_pylist() == [1.0, None, 0.5]


@pytest.mark.parametrize("window_minutes", (5, 15, 30, 60))
def test_feature_builder_uses_each_planned_window_without_expansion(
    window_minutes: int,
) -> None:
    trade_date = "2026-05-06"
    window_start = datetime(
        2026, 5, 6, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai")
    ) - timedelta(minutes=window_minutes)
    minutes = _minute_table(
        trade_date,
        [
            _row("000001", window_start.time(), notional=1_000.0),
            _row("000001", time(14, 29), notional=1_100.0),
            _row("600000", window_start.time(), notional=2_000.0),
            _row("600000", time(14, 29), notional=2_000.0),
            _row(
                "300001",
                (window_start - timedelta(minutes=1)).time(),
                notional=1_000_000.0,
            ),
            _row("300001", time(14, 29), notional=3_000.0),
        ],
    )

    output = build_stock_1430_features(
        minutes, trade_date=date.fromisoformat(trade_date)
    )
    frame = output.to_pandas().set_index("symbol")

    assert output.column(
        f"f_l2_edge_vwap_return_rank_{window_minutes}m"
    ).to_pylist() == [1.0, None, 0.5]
    assert frame[f"f_l2_notional_rank_{window_minutes}m"].tolist() == [
        1 / 3,
        2 / 3,
        1.0,
    ]
    assert frame[f"f_l2_observed_minute_ratio_{window_minutes}m"].tolist() == [
        2 / window_minutes,
        1 / window_minutes,
        2 / window_minutes,
    ]


@pytest.mark.parametrize("changed_side", ("entry", "exit"))
@pytest.mark.parametrize(
    ("outside_minute", "outside_phase"),
    (
        (time(14, 30), MarketPhase.CONTINUOUS),
        (time(14, 36), MarketPhase.CONTINUOUS),
        (time(14, 32), MarketPhase.AUCTION),
    ),
)
def test_label_builder_ignores_rows_outside_entry_and_exit_windows(
    changed_side: str,
    outside_minute: time,
    outside_phase: MarketPhase,
) -> None:
    trade_date = "2026-05-06"
    next_trade_date = "2026-05-07"
    symbols = ("000001", "600000")
    features = _feature_keys(trade_date, symbols)
    factors_t = _factor_frame(trade_date, symbols)
    factors_t1 = _factor_frame(next_trade_date, symbols)
    base_entry = [_row(symbol, time(14, 31)) for symbol in symbols]
    base_exit = [
        _row("000001", time(14, 35), notional=1_200.0),
        _row("600000", time(14, 35), notional=1_100.0),
    ]
    expected = build_stock_1430_labels(
        feature_keys=features,
        entry_minutes=_minute_table(trade_date, base_entry),
        exit_minutes=_minute_table(next_trade_date, base_exit),
        entry_factors=factors_t,
        exit_factors=factors_t1,
        trade_date=date.fromisoformat(trade_date),
        next_trade_date=date.fromisoformat(next_trade_date),
    )

    assert expected.column("y_rank_return").to_pylist() == [1.0, 0.5]
    changed_rows = base_entry if changed_side == "entry" else base_exit
    changed_rows.append(
        _row(
            "000001" if changed_side == "entry" else "600000",
            outside_minute,
            phase=outside_phase,
            notional=99_000.0,
        )
    )
    actual = build_stock_1430_labels(
        feature_keys=features,
        entry_minutes=_minute_table(trade_date, base_entry),
        exit_minutes=_minute_table(next_trade_date, base_exit),
        entry_factors=factors_t,
        exit_factors=factors_t1,
        trade_date=date.fromisoformat(trade_date),
        next_trade_date=date.fromisoformat(next_trade_date),
    )

    assert actual.equals(expected)


def test_label_builder_applies_each_dates_factor_and_average_tie_rank() -> None:
    trade_date = "2026-05-06"
    next_trade_date = "2026-05-07"
    symbols = ("000001", "300001", "600000")
    entry_factors = _factor_frame(trade_date, symbols)
    entry_factors.loc[:, "adj_factor"] = [2.0, 1.0, 2.0]
    exit_factors = _factor_frame(next_trade_date, symbols)
    exit_factors.loc[:, "adj_factor"] = [4.0, 1.0, 2.0]
    original_entry_factors = entry_factors.copy(deep=True)
    original_exit_factors = exit_factors.copy(deep=True)
    features = _feature_keys(trade_date, symbols)

    output = build_stock_1430_labels(
        feature_keys=features,
        entry_minutes=_minute_table(
            trade_date,
            [
                _row("000001", time(14, 31), notional=1_000.0),
                _row("300001", time(14, 31), notional=800.0),
                _row("600000", time(14, 31), notional=500.0),
            ],
        ),
        exit_minutes=_minute_table(
            next_trade_date,
            [
                _row("000001", time(14, 35), notional=600.0),
                _row("300001", time(14, 35), notional=1_000.0),
                _row("600000", time(14, 35), notional=600.0),
            ],
        ),
        entry_factors=entry_factors,
        exit_factors=exit_factors,
        trade_date=date.fromisoformat(trade_date),
        next_trade_date=date.fromisoformat(next_trade_date),
    )

    assert output.column("y_rank_return").to_pylist() == [0.5, 1.0, 0.5]
    assert output.select(features.column_names).equals(features)
    pd.testing.assert_frame_equal(entry_factors, original_entry_factors)
    pd.testing.assert_frame_equal(exit_factors, original_exit_factors)


@pytest.mark.parametrize("weighted_side", ("entry", "exit"))
def test_label_builder_uses_volume_weighted_window_prices(weighted_side: str) -> None:
    trade_date = "2026-05-06"
    next_trade_date = "2026-05-07"
    symbols = ("000001", "600000")
    entry_rows = [_row(symbol, time(14, 31)) for symbol in symbols]
    exit_rows = [
        _row("000001", time(14, 31), notional=1_800.0),
        _row("600000", time(14, 31), notional=1_100.0),
    ]
    if weighted_side == "entry":
        entry_rows.append(_row("000001", time(14, 35), volume=300, notional=6_000.0))
        expected_ranks = [0.5, 1.0]
    else:
        exit_rows[0] = _row("000001", time(14, 31), notional=500.0)
        exit_rows.append(_row("000001", time(14, 35), volume=300, notional=4_500.0))
        expected_ranks = [1.0, 0.5]

    output = build_stock_1430_labels(
        feature_keys=_feature_keys(trade_date, symbols),
        entry_minutes=_minute_table(trade_date, entry_rows),
        exit_minutes=_minute_table(next_trade_date, exit_rows),
        entry_factors=_factor_frame(trade_date, symbols),
        exit_factors=_factor_frame(next_trade_date, symbols),
        trade_date=date.fromisoformat(trade_date),
        next_trade_date=date.fromisoformat(next_trade_date),
    )

    assert output.column("y_rank_return").to_pylist() == expected_ranks


@pytest.mark.parametrize(
    "invalid_factor", (None, 0.0, -1.0, float("inf"), float("nan"))
)
@pytest.mark.parametrize("invalid_side", ("entry", "exit"))
def test_label_builder_preserves_invalid_factor_rows_as_null(
    invalid_factor: float | None,
    invalid_side: str,
) -> None:
    trade_date = "2026-05-06"
    next_trade_date = "2026-05-07"
    symbols = ("000001", "600000")
    entry_factors = _factor_frame(trade_date, symbols)
    exit_factors = _factor_frame(next_trade_date, symbols)
    owned_factors = entry_factors if invalid_side == "entry" else exit_factors
    owned_factors.loc[0, "adj_factor"] = invalid_factor
    features = _feature_keys(trade_date, symbols)
    rows = [_row(symbol, time(14, 31)) for symbol in symbols]

    output = build_stock_1430_labels(
        feature_keys=features,
        entry_minutes=_minute_table(trade_date, rows),
        exit_minutes=_minute_table(next_trade_date, rows),
        entry_factors=entry_factors,
        exit_factors=exit_factors,
        trade_date=date.fromisoformat(trade_date),
        next_trade_date=date.fromisoformat(next_trade_date),
    )

    assert output.select(features.column_names).equals(features)
    assert output.column("y_rank_return").to_pylist() == [None, 1.0]


def test_label_builder_rejects_noncanonical_feature_keys() -> None:
    trade_date = "2026-05-06"
    features = _feature_keys(trade_date, ("600000", "000001"))
    empty_minutes = pa.Table.from_batches([], schema=_MINUTE_SCHEMA)

    with pytest.raises(ValueError, match="canonical order"):
        build_stock_1430_labels(
            feature_keys=features,
            entry_minutes=empty_minutes,
            exit_minutes=empty_minutes,
            entry_factors=_factor_frame(trade_date, ()),
            exit_factors=_factor_frame("2026-05-07", ()),
            trade_date=date.fromisoformat(trade_date),
            next_trade_date=date.fromisoformat("2026-05-07"),
        )


@pytest.mark.parametrize(
    ("invalid_key", "message"),
    (
        ("duplicate", "unique"),
        ("date", "trade_date does not match"),
        ("decision", "decision_ts_utc does not match"),
        ("missing", "decision_ts_utc"),
        ("type", "invalid column types"),
        ("null", "symbol"),
    ),
)
def test_label_builder_validates_persisted_feature_key_identity(
    invalid_key: str,
    message: str,
) -> None:
    trade_date = "2026-05-06"
    keys = _feature_keys(trade_date, ("000001",))
    if invalid_key == "duplicate":
        keys = pa.concat_tables([keys, keys])
    elif invalid_key == "date":
        keys = keys.set_column(1, "trade_date", pa.array(["2026-05-07"]))
    elif invalid_key == "decision":
        keys = keys.set_column(2, "decision_ts_utc", pa.array([1], type=pa.int64()))
    elif invalid_key == "missing":
        keys = keys.drop(["decision_ts_utc"])
    elif invalid_key == "type":
        keys = keys.set_column(2, "decision_ts_utc", pa.array([1.0], type=pa.float64()))
    else:
        keys = keys.set_column(0, "symbol", pa.array([None], type=pa.string()))
    empty_minutes = pa.Table.from_batches([], schema=_MINUTE_SCHEMA)

    with pytest.raises(ValueError, match=message):
        build_stock_1430_labels(
            feature_keys=keys,
            entry_minutes=empty_minutes,
            exit_minutes=empty_minutes,
            entry_factors=_factor_frame(trade_date, ()),
            exit_factors=_factor_frame("2026-05-07", ()),
            trade_date=date(2026, 5, 6),
            next_trade_date=date(2026, 5, 7),
        )


def test_label_builder_keeps_rows_when_every_supervision_value_is_missing() -> None:
    trade_date = "2026-05-06"
    next_trade_date = "2026-05-07"
    empty_minutes = pa.Table.from_batches([], schema=_MINUTE_SCHEMA)

    output = build_stock_1430_labels(
        feature_keys=_feature_keys(trade_date, ("000001",)),
        entry_minutes=empty_minutes,
        exit_minutes=empty_minutes,
        entry_factors=_factor_frame(trade_date, ()),
        exit_factors=_factor_frame(next_trade_date, ()),
        trade_date=date.fromisoformat(trade_date),
        next_trade_date=date.fromisoformat(next_trade_date),
    )

    assert output.column("symbol").to_pylist() == ["000001"]
    assert output.column("y_rank_return").to_pylist() == [None]


class _MinuteRow(TypedDict):
    symbol: str
    minute: time
    phase: int
    open: float
    high: float
    low: float
    close: float
    volume_sum: int
    notional_sum: float
    trade_count: int
    tick_signed_volume_sum: int
    tick_signed_notional_sum: float


def _row(
    symbol: str,
    minute: time,
    *,
    phase: MarketPhase = MarketPhase.CONTINUOUS,
    high: float = 10.0,
    low: float = 10.0,
    volume: int = 100,
    notional: float = 1_000.0,
    trades: int = 1,
    signed_volume: int = 0,
    signed_notional: float = 0.0,
) -> _MinuteRow:
    return {
        "symbol": symbol,
        "minute": minute,
        "phase": int(phase),
        "open": low,
        "high": high,
        "low": low,
        "close": high,
        "volume_sum": volume,
        "notional_sum": notional,
        "trade_count": trades,
        "tick_signed_volume_sum": signed_volume,
        "tick_signed_notional_sum": signed_notional,
    }


def _minute_table(
    trade_date: str,
    rows: Sequence[_MinuteRow],
) -> pa.Table:
    values = {field.name: [] for field in _MINUTE_SCHEMA}
    for row in rows:
        values["symbol"].append(row["symbol"])
        values["trade_date"].append(trade_date)
        values["minute_start_ts_utc"].append(
            DateTimeUtils.local_time_to_utc_epoch_us(
                row["minute"],
                date.fromisoformat(trade_date),
            )
        )
        for column in (
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
        ):
            values[column].append(row[column])
    return pa.Table.from_pydict(values, schema=_MINUTE_SCHEMA)


def _feature_keys(trade_date: str, symbols: tuple[str, ...]) -> pa.Table:
    decision_ts_utc = DateTimeUtils.local_time_to_utc_epoch_us(
        time(14, 30),
        date.fromisoformat(trade_date),
    )
    return pa.Table.from_pydict(
        {
            "symbol": list(symbols),
            "trade_date": [trade_date] * len(symbols),
            "decision_ts_utc": [decision_ts_utc] * len(symbols),
        },
        schema=pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("trade_date", pa.string(), nullable=False),
                pa.field("decision_ts_utc", pa.int64(), nullable=False),
            ]
        ),
    )


def _factor_frame(trade_date: str, symbols: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": pd.Series(symbols, dtype="string"),
            "trade_date": pd.Series([trade_date] * len(symbols), dtype="string"),
            "adj_factor": pd.Series([1.0] * len(symbols), dtype="float64"),
        }
    )
