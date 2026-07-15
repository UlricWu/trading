# filepath: tests/utils/test_datetime_utils.py
from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from src.utils.datetime_utils import DateTimeUtils, TradingSession


def test_parse_utc_requires_aware_datetime_and_rejects_bool() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        DateTimeUtils.parse_utc(datetime(2026, 7, 15, 9, 30))

    with pytest.raises(TypeError, match="timestamp"):
        DateTimeUtils.parse_utc(True)  # type: ignore[arg-type]


def test_parse_tick_time_converts_milliseconds_to_microseconds() -> None:
    assert DateTimeUtils.parse_tick_time("93000123") == (9, 30, 0, 123_000)
    assert DateTimeUtils.parse_tick_time(143000001) == (14, 30, 0, 1_000)

    with pytest.raises(ValueError, match="invalid time"):
        DateTimeUtils.parse_tick_time("96000000")


def test_local_time_round_trip_uses_explicit_epoch_unit() -> None:
    timestamp_microseconds = DateTimeUtils.local_time_to_utc_ts(
        time(9, 30),
        date(2026, 7, 15),
        unit="us",
    )

    assert (
        DateTimeUtils.to_local(
            DateTimeUtils.parse_utc(timestamp_microseconds, unit="us")
        ).isoformat()
        == "2026-07-15T09:30:00+08:00"
    )


def test_trading_calendar_inputs_are_explicit_and_do_not_create_global_state() -> None:
    utc_instant = datetime(2026, 7, 15, 1, 30, tzinfo=UTC)
    morning_session = TradingSession(opens_at=time(9, 30), closes_at=time(11, 30))

    assert DateTimeUtils.is_trading_time_utc(
        utc_instant,
        trading_sessions=(morning_session,),
    )
    assert DateTimeUtils.is_trading_day_utc(
        utc_instant,
        trading_days=(date(2026, 7, 15),),
    )
    assert not DateTimeUtils.is_trading_day_utc(
        utc_instant,
        trading_days=(),
    )


def test_extract_date_rejects_invalid_delimiters_and_calendar_values() -> None:
    assert DateTimeUtils.extract_date("2026/07/15 09:30:00") == date(2026, 7, 15)

    with pytest.raises(ValueError, match="cannot extract"):
        DateTimeUtils.extract_date("2026x07x15")
    with pytest.raises(ValueError, match="invalid calendar date"):
        DateTimeUtils.extract_date("2026-02-30")
