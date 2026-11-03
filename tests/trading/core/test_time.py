# filepath: tests/trading/core/test_time.py
from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from src.trading.core.time import (
    BarTime, TradingWindow, advance_trading_minutes, trading_day_exit_ts,
)
from src.utils.datetime_utils import DateTimeUtils


def _ts(day: str, clock: str) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(time.fromisoformat(clock), date.fromisoformat(day))


@pytest.fixture
def windows() -> tuple[TradingWindow, ...]:
    return tuple(TradingWindow(day, _ts(day, start), _ts(day, end))
                 for day in ("2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08")
                 for start, end in (("09:30", "11:30"), ("13:00", "15:00")))


@pytest.mark.parametrize("start,end_day,end", [
    ("10:00", "2026-07-03", "10:03"),
    ("11:29", "2026-07-03", "13:02"),
    ("11:29:30", "2026-07-03", "13:02:30"),
    ("11:29:30.123456", "2026-07-03", "13:02:30.123456"),
    ("14:59", "2026-07-06", "09:32"),
    ("11:27", "2026-07-03", "11:30"),
    ("11:30", "2026-07-03", "13:03"),
    ("12:00", "2026-07-03", "13:03"),
])
def test_trading_minutes_skip_lunch_and_closed_dates(
    windows: tuple[TradingWindow, ...], start: str, end_day: str, end: str,
) -> None:
    actual = advance_trading_minutes(start_ts_us=_ts("2026-07-03", start), minutes=3, windows=windows)
    assert actual == _ts(end_day, end)


def test_missing_future_windows_fail(windows: tuple[TradingWindow, ...]) -> None:
    with pytest.raises(ValueError, match="cover"):
        advance_trading_minutes(start_ts_us=_ts("2026-07-08", "14:59"), minutes=3, windows=windows)


def test_trading_days_count_entry_as_zero_and_require_coverage() -> None:
    dates = ("2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08")
    assert trading_day_exit_ts(entry_trade_date=dates[0], holding_days=3, exit_time=time(14, 30),
        open_dates=dates, timezone=ZoneInfo("Asia/Shanghai")) == _ts(dates[-1], "14:30")
    with pytest.raises(ValueError, match="cover"):
        trading_day_exit_ts(entry_trade_date=dates[0], holding_days=4, exit_time=time(14, 30),
            open_dates=dates, timezone=ZoneInfo("Asia/Shanghai"))


def test_complete_bar_time_supports_late_availability_without_granting_execution() -> None:
    bar = BarTime("minute", 1, "2026-07-03", 0, 60_000_000, 60_000_002)
    assert bar.available_ts_us > bar.end_ts_us
    with pytest.raises(ValueError, match="start < end"):
        BarTime("minute", 1, "2026-07-03", 0, 60_000_000, 1)
    with pytest.raises(ValueError, match="match declared size"):
        BarTime("minute", 1, "2026-07-03", 0, 1, 1)
