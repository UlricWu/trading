# filepath: tests/data_system/builders/test_previous_day.py
"""Prior-state qualification and next-day high target boundaries."""

from datetime import date, time

import numpy as np
import pandas as pd
import pyarrow as pa

from src.data_system.builders.previous_day import build_next_day_high_labels, qualify_previous_day
from src.utils.datetime_utils import DateTimeUtils


def test_resumption_ended_halt_unknown_membership_and_conflicting_status() -> None:
    symbols = ["normal", "st", "halted", "resumed", "intraday", "conflict", "future", "missing"]
    listed = pd.DataFrame({"symbol": symbols[:-1], "list_date": ["2020-01-01"] * 6 + ["2026-07-02"]})
    events = pd.DataFrame([
        ("halted", "S", None), ("resumed", "R", None), ("intraday", "S", "10:00-10:10"),
        ("conflict", "S", None), ("conflict", "R", None),
    ], columns=["symbol", "suspend_type", "suspend_timing"])
    result = qualify_previous_day(symbols, listed=listed, st=pd.DataFrame({"symbol": ["st"]}),
        suspension=events, target_date="2026-07-01")
    assert result.loc[result.eligible, "symbol"].tolist() == ["normal", "resumed", "intraday"]
    assert result.set_index("symbol").reason["missing"] == "source_uncovered"
    assert result.set_index("symbol").reason["conflict"] == "suspension_unknown"


def test_label_uses_right_open_entry_auction_high_adjustment_and_unknowns() -> None:
    day = date(2026, 7, 3)
    entry = pa.table({"symbol": ["a", "a", "b"], "phase": pa.array([2, 2, 2], type=pa.int8()),
        "minute_start_ts_utc": [DateTimeUtils.local_time_to_utc_epoch_us(t, day) for t in (time(14, 31), time(14, 36), time(14, 31))],
        "notional_sum": [100.0, 1000.0, 200.0], "volume_sum": [10, 10, 10]})
    following = pa.table({"symbol": ["a", "a", "b"], "high": [6.0, 5.0, 24.0], "phase": [0, 2, 2]})
    result = build_next_day_high_labels(symbols=("a", "b", "missing"), entry_minutes=entry, exit_minutes=following,
        entry_factors=pd.DataFrame({"symbol": ["a", "b"], "adj_factor": [1.0, 1.0]}),
        exit_factors=pd.DataFrame({"symbol": ["a", "b"], "adj_factor": [2.0, 1.0]}),
        trade_date="2026-07-03", next_trade_date="2026-07-06")
    np.testing.assert_array_equal(result.y_rank_return, [0.75, 0.75, np.nan])
    np.testing.assert_array_equal(result.y_event, [0.0, 0.0, np.nan])
