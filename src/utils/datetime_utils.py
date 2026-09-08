# filepath: src/utils/datetime_utils.py
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


class DateTimeUtils:
    """Provide the system's shared scalar date and time operations."""

    MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")

    @staticmethod
    def require_system_date(
        value: object,
        *,
        field_name: str = "date",
    ) -> str:
        """Return a strict, valid ``YYYY-MM-DD`` system date."""
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a str in YYYY-MM-DD format")
        if value.strip() != value:
            raise ValueError(
                f"{field_name} must not contain leading or trailing spaces"
            )
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError(f"{field_name} must use YYYY-MM-DD format")
        if not (
            value[0:4].isdigit() and value[5:7].isdigit() and value[8:10].isdigit()
        ):
            raise ValueError(f"{field_name} must use YYYY-MM-DD format")

        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid calendar date") from exc
        if parsed.isoformat() != value:
            raise ValueError(f"{field_name} must use YYYY-MM-DD format")
        return value

    @staticmethod
    def normalize_source_date(
        value: object,
        *,
        field_name: str = "date",
    ) -> str:
        """Convert a supported source-native date to ``YYYY-MM-DD``."""
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, bool):
            raise TypeError(f"{field_name} must be a source-native date value")

        if isinstance(value, int):
            source_value = str(value)
        elif isinstance(value, str):
            source_value = value
        else:
            raise TypeError(f"{field_name} must be a source-native date value")

        if (
            len(source_value) == 10
            and source_value[4] == "-"
            and source_value[7] == "-"
        ):
            return DateTimeUtils.require_system_date(
                source_value,
                field_name=field_name,
            )

        if (
            len(source_value) == 10
            and source_value[4] == "/"
            and source_value[7] == "/"
        ):
            normalized = f"{source_value[0:4]}-{source_value[5:7]}-{source_value[8:10]}"
        elif len(source_value) == 8 and source_value.isdigit():
            normalized = f"{source_value[0:4]}-{source_value[4:6]}-{source_value[6:8]}"
        else:
            raise ValueError(f"{field_name} must be a source-native date value")

        try:
            return date.fromisoformat(normalized).isoformat()
        except ValueError as exc:
            raise ValueError(
                f"{field_name} must be a valid source-native date"
            ) from exc

    @staticmethod
    def to_compact_date(
        value: object,
        *,
        field_name: str = "date",
    ) -> str:
        """Convert a strict system date to ``YYYYMMDD``."""
        return DateTimeUtils.require_system_date(
            value,
            field_name=field_name,
        ).replace("-", "")

    @staticmethod
    def date_range(start: object, end: object) -> list[str]:
        """Return the inclusive system-date range from start through end."""
        start_value = DateTimeUtils.require_system_date(start, field_name="start")
        end_value = DateTimeUtils.require_system_date(end, field_name="end")
        current = date.fromisoformat(start_value)
        end_date = date.fromisoformat(end_value)
        if current > end_date:
            raise ValueError(
                f"invalid date range: start={start_value}, end={end_value}"
            )

        values: list[str] = []
        while current <= end_date:
            values.append(current.isoformat())
            current += timedelta(days=1)
        return values

    @staticmethod
    def days_before(
        value: object,
        days: int,
        *,
        field_name: str = "date",
    ) -> str:
        """Return the system date a positive number of calendar days earlier."""
        if not isinstance(days, int) or isinstance(days, bool):
            raise TypeError("days must be an int")
        if days <= 0:
            raise ValueError("days must be > 0")

        current = date.fromisoformat(
            DateTimeUtils.require_system_date(value, field_name=field_name)
        )
        return (current - timedelta(days=days)).isoformat()

    @staticmethod
    def from_utc_epoch_us(value: int) -> datetime:
        """Convert UTC epoch microseconds to an aware UTC datetime."""
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError("value must be an int UTC epoch microsecond timestamp")

        try:
            return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)
        except OverflowError as exc:
            raise ValueError("value is outside the supported datetime range") from exc

    @staticmethod
    def to_local(
        value: datetime,
        timezone: ZoneInfo | None = None,
    ) -> datetime:
        """Convert an aware UTC datetime to an explicit local timezone."""
        if not isinstance(value, datetime):
            raise TypeError("value must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("value must be a timezone-aware UTC datetime")
        if value.utcoffset() != timedelta(0):
            raise ValueError("value must be a UTC datetime")
        if timezone is None:
            timezone = DateTimeUtils.MARKET_TIMEZONE
        if not isinstance(timezone, ZoneInfo):
            raise TypeError("timezone must be zoneinfo.ZoneInfo")
        return value.astimezone(timezone)

    @staticmethod
    def local_time_to_utc_epoch_us(
        local_time: time,
        trade_date: date,
        timezone: ZoneInfo | None = None,
    ) -> int:
        """Convert a unique local wall-clock time to UTC epoch microseconds."""
        if not isinstance(local_time, time):
            raise TypeError("local_time must be datetime.time")
        if local_time.tzinfo is not None:
            raise ValueError("local_time must be a naive wall-clock time")
        if not isinstance(trade_date, date) or isinstance(trade_date, datetime):
            raise TypeError("trade_date must be datetime.date")
        if timezone is None:
            timezone = DateTimeUtils.MARKET_TIMEZONE
        if not isinstance(timezone, ZoneInfo):
            raise TypeError("timezone must be zoneinfo.ZoneInfo")

        wall_clock = datetime.combine(trade_date, local_time).replace(fold=0)
        first_local = wall_clock.replace(tzinfo=timezone, fold=0)
        second_local = wall_clock.replace(tzinfo=timezone, fold=1)
        first_utc = first_local.astimezone(UTC)
        second_utc = second_local.astimezone(UTC)
        first_valid = (
            first_utc.astimezone(timezone).replace(tzinfo=None, fold=0) == wall_clock
        )
        second_valid = (
            second_utc.astimezone(timezone).replace(tzinfo=None, fold=0) == wall_clock
        )

        if not first_valid and not second_valid:
            raise ValueError("local wall-clock time does not exist in timezone")
        if (
            first_valid
            and second_valid
            and first_local.utcoffset() != second_local.utcoffset()
        ):
            raise ValueError("local wall-clock time is ambiguous in timezone")

        utc_value = first_utc if first_valid else second_utc
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        delta = utc_value - epoch
        return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds

    @staticmethod
    def now_utc() -> datetime:
        """Return the current aware UTC datetime."""
        return datetime.now(UTC)

    @staticmethod
    def now() -> datetime:
        """Return the current aware market-local datetime."""
        return DateTimeUtils.now_utc().astimezone(DateTimeUtils.MARKET_TIMEZONE)

    @staticmethod
    def today() -> str:
        """Return the current market-local system date."""
        return DateTimeUtils.now().date().isoformat()


__all__ = ["DateTimeUtils"]
