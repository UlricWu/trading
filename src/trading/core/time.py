# filepath: src/trading/core/time.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time
from typing import Literal
from zoneinfo import ZoneInfo

from src.utils.datetime_utils import DateTimeUtils

US_PER_SECOND = 1_000_000
US_PER_MINUTE = 60 * US_PER_SECOND


def is_minute_boundary(ts_us: int) -> bool:
    return int(ts_us) % US_PER_MINUTE == 0


@dataclass(frozen=True, slots=True)
class BarTime:
    """Source-declared complete observation interval and availability.

    Example:
        bar = BarTime("minute", 1, "2026-07-03", minute_start_us, minute_end_us, minute_end_us)
    """

    unit: Literal["minute", "trading_day"]
    size: int
    trade_date: str
    start_ts_us: int
    end_ts_us: int
    available_ts_us: int

    def __post_init__(self) -> None:
        DateTimeUtils.require_system_date(self.trade_date, field_name="trade_date")
        if self.unit not in {"minute", "trading_day"} or type(self.size) is not int or self.size <= 0:
            raise ValueError("bar unit and positive integer size are required")
        if not self.start_ts_us < self.end_ts_us <= self.available_ts_us:
            raise ValueError("bar requires start < end <= available")
        if self.unit == "minute" and self.end_ts_us - self.start_ts_us != self.size * US_PER_MINUTE:
            raise ValueError("minute interval must match declared size")
        if self.unit == "trading_day" and self.size != 1:
            raise ValueError("only single trading-day bars are supported")


@dataclass(frozen=True, slots=True)
class TradingWindow:
    """A source-declared interval counted toward trading-minute holding time.

    Example:
        window = TradingWindow("2026-07-03", morning_open_us, morning_close_us)
    """

    trade_date: str
    start_ts_us: int
    end_ts_us: int

    def __post_init__(self) -> None:
        DateTimeUtils.require_system_date(self.trade_date, field_name="trade_date")
        if self.start_ts_us >= self.end_ts_us:
            raise ValueError("trading window requires start < end")


def advance_trading_minutes(
    *, start_ts_us: int, minutes: int, windows: Sequence[TradingWindow],
) -> int:
    """Count trading minutes over prepared sorted, nonoverlapping windows.

    Example:
        due = advance_trading_minutes(start_ts_us=entry_ts, minutes=3, windows=windows)
    """
    if type(minutes) is not int or minutes <= 0:
        raise ValueError("minutes must be a positive integer")
    remaining_us = minutes * US_PER_MINUTE
    for window in windows:
        cursor = max(start_ts_us, window.start_ts_us)
        if cursor >= window.end_ts_us:
            continue
        available = window.end_ts_us - cursor
        if remaining_us <= available:
            return cursor + remaining_us
        remaining_us -= available
    raise ValueError("trading windows do not cover the holding period")


def trading_day_exit_ts(
    *, entry_trade_date: str, holding_days: int, exit_time: time,
    open_dates: Sequence[str], timezone: ZoneInfo,
) -> int:
    """Resolve day N after the entry day, which is day zero.

    Example:
        due = trading_day_exit_ts(entry_trade_date="2026-07-03", holding_days=1,
            exit_time=time(14, 30), open_dates=("2026-07-03", "2026-07-06"),
            timezone=ZoneInfo("Asia/Shanghai"))
    """
    if type(holding_days) is not int or holding_days <= 0:
        raise ValueError("holding_days must be a positive integer")
    index = open_dates.index(entry_trade_date) + holding_days
    if index >= len(open_dates):
        raise ValueError("trade calendar does not cover the holding period")
    return DateTimeUtils.local_time_to_utc_epoch_us(
        local_time=exit_time, trade_date=date.fromisoformat(open_dates[index]), timezone=timezone,
    )
