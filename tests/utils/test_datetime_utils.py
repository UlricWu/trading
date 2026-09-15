# filepath: tests/utils/test_datetime_utils.py
from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from src.utils.datetime_utils import DateTimeUtils


@pytest.mark.parametrize(
    "value",
    [
        "2026-05-06",
        "2024-02-29",
    ],
)
def test_require_system_date_accepts_strict_dates(value: str) -> None:
    assert DateTimeUtils.require_system_date(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "20260506",
        "2026/05/06",
        "2026-5-6",
        "2026-02-30",
        " 2026-05-06",
        "2026-05-06 ",
    ],
)
def test_require_system_date_rejects_invalid_strings(value: str) -> None:
    with pytest.raises(ValueError):
        DateTimeUtils.require_system_date(value)


def test_require_system_date_rejects_non_string_with_field_name() -> None:
    with pytest.raises(TypeError, match="trade_date"):
        DateTimeUtils.require_system_date(20260506, field_name="trade_date")


@pytest.mark.parametrize(
    ("source_value", "expected"),
    [
        ("2026-05-06", "2026-05-06"),
        ("2026/05/06", "2026-05-06"),
        ("20260506", "2026-05-06"),
        (20260506, "2026-05-06"),
        (date(2026, 5, 6), "2026-05-06"),
        (datetime(2026, 5, 6, 9, 30), "2026-05-06"),
    ],
)
def test_normalize_source_date_accepts_defined_values(
    source_value: object,
    expected: str,
) -> None:
    assert DateTimeUtils.normalize_source_date(source_value) == expected


def test_normalize_source_date_does_not_change_aware_datetime_timezone() -> None:
    source_value = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)

    assert DateTimeUtils.normalize_source_date(source_value) == "2026-01-01"


@pytest.mark.parametrize(
    ("source_value", "error_type"),
    [
        (True, TypeError),
        (None, TypeError),
        (object(), TypeError),
        ("20260230", ValueError),
        ("2026050601", ValueError),
        (2026050601, ValueError),
        (" 20260506", ValueError),
    ],
)
def test_normalize_source_date_rejects_undefined_values(
    source_value: object,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        DateTimeUtils.normalize_source_date(source_value)


def test_to_compact_date_requires_a_system_date() -> None:
    assert DateTimeUtils.to_compact_date("2026-05-06") == "20260506"

    with pytest.raises(ValueError):
        DateTimeUtils.to_compact_date("20260506")


def test_date_range_returns_an_inclusive_range() -> None:
    assert DateTimeUtils.date_range("2026-05-01", "2026-05-03") == [
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
    ]


def test_date_range_rejects_reverse_ranges() -> None:
    with pytest.raises(ValueError, match="invalid date range"):
        DateTimeUtils.date_range("2026-05-03", "2026-05-01")


def test_days_before_uses_calendar_days() -> None:
    assert DateTimeUtils.days_before("2026-05-06", 5) == "2026-05-01"
    assert DateTimeUtils.days_before("2024-03-01", 1) == "2024-02-29"


@pytest.mark.parametrize("days", [True, 1.5, "1", None])
def test_days_before_rejects_non_integer_days(days: object) -> None:
    with pytest.raises(TypeError, match="days must be an int"):
        DateTimeUtils.days_before("2026-05-06", cast("int", days))


@pytest.mark.parametrize("days", [0, -1])
def test_days_before_rejects_non_positive_days(days: int) -> None:
    with pytest.raises(ValueError, match="days must be > 0"):
        DateTimeUtils.days_before("2026-05-06", days)


def test_from_utc_epoch_us_preserves_microseconds() -> None:
    assert DateTimeUtils.from_utc_epoch_us(0) == datetime(1970, 1, 1, tzinfo=UTC)
    assert DateTimeUtils.from_utc_epoch_us(-1) == datetime(
        1969,
        12,
        31,
        23,
        59,
        59,
        999999,
        tzinfo=UTC,
    )
    assert DateTimeUtils.from_utc_epoch_us(1762737300040000) == datetime(
        2025,
        11,
        10,
        1,
        15,
        0,
        40000,
        tzinfo=UTC,
    )


@pytest.mark.parametrize("value", [True, 1.5, "1762737300040000"])
def test_from_utc_epoch_us_rejects_non_integer_values(value: object) -> None:
    with pytest.raises(TypeError, match="UTC epoch microsecond"):
        DateTimeUtils.from_utc_epoch_us(cast("int", value))


def test_from_utc_epoch_us_rejects_values_outside_datetime_range() -> None:
    with pytest.raises(ValueError, match="supported datetime range"):
        DateTimeUtils.from_utc_epoch_us(10**30)


def test_to_local_uses_default_market_timezone() -> None:
    local_value = DateTimeUtils.to_local(datetime(2025, 11, 10, 1, 15, tzinfo=UTC))

    assert local_value.isoformat() == "2025-11-10T09:15:00+08:00"
    assert local_value.tzinfo == DateTimeUtils.MARKET_TIMEZONE


def test_to_local_accepts_an_explicit_timezone() -> None:
    new_york = ZoneInfo("America/New_York")

    local_value = DateTimeUtils.to_local(
        datetime(2025, 1, 1, tzinfo=UTC),
        timezone=new_york,
    )

    assert local_value.isoformat() == "2024-12-31T19:00:00-05:00"
    assert local_value.tzinfo == new_york


def test_to_local_requires_an_aware_utc_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        DateTimeUtils.to_local(datetime(2025, 1, 1))

    with pytest.raises(ValueError, match="must be a UTC datetime"):
        DateTimeUtils.to_local(
            datetime(2025, 1, 1, tzinfo=DateTimeUtils.MARKET_TIMEZONE)
        )


def test_to_local_requires_zoneinfo_for_the_destination() -> None:
    with pytest.raises(TypeError, match="zoneinfo.ZoneInfo"):
        DateTimeUtils.to_local(
            datetime(2025, 1, 1, tzinfo=UTC),
            timezone=cast("ZoneInfo", UTC),
        )


def test_local_time_to_utc_epoch_us_preserves_microseconds() -> None:
    assert (
        DateTimeUtils.local_time_to_utc_epoch_us(
            time(9, 15, 0, 40000),
            date(2025, 11, 10),
        )
        == 1762737300040000
    )


def test_local_time_to_utc_epoch_us_accepts_an_explicit_timezone() -> None:
    assert (
        DateTimeUtils.local_time_to_utc_epoch_us(
            time(9, 30),
            date(2025, 1, 2),
            timezone=ZoneInfo("America/New_York"),
        )
        == 1735828200000000
    )


def test_local_time_to_utc_epoch_us_rejects_invalid_inputs() -> None:
    with pytest.raises(TypeError, match="local_time"):
        DateTimeUtils.local_time_to_utc_epoch_us(
            cast("time", "09:15"),
            date(2025, 11, 10),
        )
    with pytest.raises(ValueError, match="naive wall-clock"):
        DateTimeUtils.local_time_to_utc_epoch_us(
            time(9, 15, tzinfo=UTC),
            date(2025, 11, 10),
        )
    with pytest.raises(TypeError, match="trade_date"):
        DateTimeUtils.local_time_to_utc_epoch_us(
            time(9, 15),
            cast("date", datetime(2025, 11, 10)),
        )
    with pytest.raises(TypeError, match="zoneinfo.ZoneInfo"):
        DateTimeUtils.local_time_to_utc_epoch_us(
            time(9, 15),
            date(2025, 11, 10),
            timezone=cast("ZoneInfo", UTC),
        )


@pytest.mark.parametrize(
    ("local_time", "trade_date", "message"),
    [
        (time(2, 30), date(2025, 3, 9), "does not exist"),
        (time(1, 30), date(2025, 11, 2), "ambiguous"),
    ],
)
def test_local_time_to_utc_epoch_us_rejects_dst_boundaries(
    local_time: time,
    trade_date: date,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        DateTimeUtils.local_time_to_utc_epoch_us(
            local_time,
            trade_date,
            timezone=ZoneInfo("America/New_York"),
        )


def test_current_time_uses_aware_utc_and_market_values() -> None:
    utc_now = DateTimeUtils.now_utc()
    market_now = DateTimeUtils.now()

    assert utc_now.tzinfo is UTC
    assert market_now.tzinfo == DateTimeUtils.MARKET_TIMEZONE
    assert DateTimeUtils.require_system_date(DateTimeUtils.today())
