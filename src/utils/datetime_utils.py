# filepath: src/utils/datetime_utils.py
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

EpochUnit = Literal["s", "ms", "us", "ns"]
_COMPACT_DATE_LENGTH = 8
_SYSTEM_DATE_LENGTH = 10
_TICK_PART_COUNT = 4


@dataclass(frozen=True, slots=True)
class TradingSession:
    """One inclusive local-market trading-time interval."""

    opens_at: time
    closes_at: time

    def __post_init__(self) -> None:
        if not isinstance(self.opens_at, time) or not isinstance(self.closes_at, time):
            raise TypeError("trading session boundaries must be datetime.time values")
        if self.opens_at.tzinfo is not None or self.closes_at.tzinfo is not None:
            raise ValueError("trading session boundaries must be naive local times")
        if self.opens_at > self.closes_at:
            raise ValueError("trading session opens_at must not be after closes_at")


class DateTimeUtils:
    """Strict date, UTC epoch, timezone, and trading-calendar conversions."""

    SHANGHAI_TIMEZONE_NAME: Final[str] = "Asia/Shanghai"

    @classmethod
    def require_system_date(cls, value: object, *, field_name: str = "date") -> str:
        """Validate and return one exact ``YYYY-MM-DD`` calendar date."""
        if not isinstance(field_name, str) or not field_name:
            raise ValueError("field_name must be a non-empty string")
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a str in YYYY-MM-DD format")
        if value.strip() != value:
            raise ValueError(
                f"{field_name} must not contain leading or trailing spaces"
            )
        if len(value) != _SYSTEM_DATE_LENGTH or value[4] != "-" or value[7] != "-":
            raise ValueError(f"{field_name} must use YYYY-MM-DD format")
        if not (
            value[0:4].isdigit() and value[5:7].isdigit() and value[8:10].isdigit()
        ):
            raise ValueError(f"{field_name} must use YYYY-MM-DD format")

        try:
            parsed_date = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid calendar date") from exc
        if parsed_date.isoformat() != value:
            raise ValueError(f"{field_name} must use YYYY-MM-DD format")
        return value

    @classmethod
    def require_trade_date(cls, value: object) -> str:
        """Validate and return one system-standard ``trade_date`` value."""
        return cls.require_system_date(value, field_name="trade_date")

    @classmethod
    def to_compact_date(cls, value: object, *, field_name: str = "date") -> str:
        """Convert exact ``YYYY-MM-DD`` input to ``YYYYMMDD``."""
        return cls.require_system_date(value, field_name=field_name).replace("-", "")

    @classmethod
    def days_before(cls, value: object, days: int, *, field_name: str = "date") -> str:
        """Return the date exactly ``days`` positive calendar days before input."""
        if type(days) is not int:
            raise TypeError("days must be an int")
        if days <= 0:
            raise ValueError("days must be positive")

        current_date = date.fromisoformat(
            cls.require_system_date(value, field_name=field_name)
        )
        return (current_date - timedelta(days=days)).isoformat()

    @classmethod
    def normalize_source_date(cls, value: object, *, field_name: str = "date") -> str:
        """Normalize a supported source-native date to ``YYYY-MM-DD``."""
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, bool):
            raise TypeError(f"{field_name} must be a source-native date value")

        if isinstance(value, int):
            source_text = str(value)
        elif isinstance(value, str):
            source_text = value
        else:
            raise TypeError(f"{field_name} must be a source-native date value")

        try:
            if (
                len(source_text) >= _SYSTEM_DATE_LENGTH
                and source_text[4] in {"-", "/"}
                and source_text[7] == source_text[4]
            ):
                return date(
                    int(source_text[0:4]),
                    int(source_text[5:7]),
                    int(source_text[8:10]),
                ).isoformat()
            if (
                len(source_text) >= _COMPACT_DATE_LENGTH
                and source_text[:_COMPACT_DATE_LENGTH].isdigit()
            ):
                return date(
                    int(source_text[0:4]),
                    int(source_text[4:6]),
                    int(source_text[6:8]),
                ).isoformat()
        except ValueError as exc:
            raise ValueError(
                f"{field_name} must be a valid source-native date"
            ) from exc

        raise ValueError(f"{field_name} must be a supported source-native date value")

    @classmethod
    def parse_utc(
        cls,
        timestamp: int | datetime,
        *,
        unit: EpochUnit | None = None,
    ) -> datetime:
        """Parse an integer UTC epoch or normalize an aware datetime to UTC."""
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("timestamp datetime must be timezone-aware")
            return timestamp.astimezone(UTC)
        if type(timestamp) is not int:
            raise TypeError("timestamp must be an int or timezone-aware datetime")

        resolved_unit = unit or cls._infer_epoch_unit(timestamp)
        units_per_second = {
            "s": 1,
            "ms": 1_000,
            "us": 1_000_000,
            "ns": 1_000_000_000,
        }.get(resolved_unit)
        if units_per_second is None:
            raise ValueError(f"unsupported timestamp unit: {resolved_unit}")
        whole_seconds, subsecond_units = divmod(timestamp, units_per_second)
        microseconds = subsecond_units * 1_000_000 // units_per_second
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            seconds=whole_seconds,
            microseconds=microseconds,
        )

    @staticmethod
    def _infer_epoch_unit(timestamp: int) -> EpochUnit:
        digit_count = len(str(abs(timestamp)))
        unit_by_digits: dict[int, EpochUnit] = {
            10: "s",
            13: "ms",
            16: "us",
            19: "ns",
        }
        try:
            return unit_by_digits[digit_count]
        except KeyError as exc:
            raise ValueError(f"unrecognized UTC epoch timestamp: {timestamp}") from exc

    @classmethod
    def to_local(
        cls,
        utc_datetime: datetime,
        timezone_info: ZoneInfo | None = None,
    ) -> datetime:
        """Convert an aware UTC datetime to an explicit or Shanghai timezone."""
        if not isinstance(utc_datetime, datetime):
            raise TypeError("utc_datetime must be a datetime")
        if utc_datetime.tzinfo is None or utc_datetime.utcoffset() is None:
            raise ValueError("utc_datetime must be timezone-aware")
        if utc_datetime.utcoffset() != timedelta(0):
            raise ValueError("utc_datetime must have a zero UTC offset")
        if timezone_info is not None and not isinstance(timezone_info, ZoneInfo):
            raise TypeError("timezone_info must be zoneinfo.ZoneInfo or None")

        target_timezone = timezone_info or ZoneInfo(cls.SHANGHAI_TIMEZONE_NAME)
        return utc_datetime.astimezone(target_timezone)

    @classmethod
    def extract_date(cls, trade_time: str | datetime | int) -> date:
        """Extract a date from exchange-local text/date prefixes or UTC epochs."""
        if isinstance(trade_time, datetime):
            return trade_time.date()
        if isinstance(trade_time, bool):
            raise TypeError("trade_time must be a str, int, or datetime")
        if isinstance(trade_time, int):
            source_text = str(trade_time)
            if len(source_text) in {13, 16, 19}:
                return cls.parse_utc(trade_time).date()
            if (
                len(source_text) >= _COMPACT_DATE_LENGTH
                and source_text[:_COMPACT_DATE_LENGTH].isdigit()
            ):
                return date(
                    int(source_text[0:4]),
                    int(source_text[4:6]),
                    int(source_text[6:8]),
                )
            raise ValueError(f"invalid integer trade_time: {trade_time}")
        if not isinstance(trade_time, str):
            raise TypeError("trade_time must be a str, int, or datetime")

        source_text = trade_time.strip()
        try:
            if (
                len(source_text) >= _SYSTEM_DATE_LENGTH
                and source_text[4] in {"-", "/"}
                and source_text[7] == source_text[4]
            ):
                return date(
                    int(source_text[0:4]),
                    int(source_text[5:7]),
                    int(source_text[8:10]),
                )
            if (
                len(source_text) >= _COMPACT_DATE_LENGTH
                and source_text[:_COMPACT_DATE_LENGTH].isdigit()
            ):
                return date(
                    int(source_text[0:4]),
                    int(source_text[4:6]),
                    int(source_text[6:8]),
                )
        except ValueError as exc:
            raise ValueError(
                f"trade_time contains an invalid calendar date: {trade_time}"
            ) from exc
        raise ValueError(f"cannot extract date from trade_time: {trade_time}")

    @classmethod
    def parse_tick_time(cls, tick_time: int | str) -> tuple[int, int, int, int]:
        """Parse ``HMMSSmmm`` or ``HHMMSSmmm`` into time parts and microseconds."""
        if isinstance(tick_time, bool) or not isinstance(tick_time, (int, str)):
            raise TypeError("tick_time must be an int or str")
        source_text = str(tick_time).strip()
        if not source_text.isdigit() or len(source_text) not in {8, 9}:
            raise ValueError(f"tick_time must use HMMSSmmm or HHMMSSmmm: {tick_time}")
        normalized_text = source_text.zfill(9)
        hour = int(normalized_text[0:2])
        minute = int(normalized_text[2:4])
        second = int(normalized_text[4:6])
        microsecond = int(normalized_text[6:9]) * 1_000
        try:
            time(hour, minute, second, microsecond)
        except ValueError as exc:
            raise ValueError(
                f"tick_time contains an invalid time: {tick_time}"
            ) from exc
        return hour, minute, second, microsecond

    @classmethod
    def local_time_to_utc_ts(
        cls,
        local_time: time,
        trade_date: date,
        exchange_timezone: ZoneInfo | None = None,
        *,
        unit: EpochUnit = "us",
    ) -> int:
        """Convert an exchange-local wall-clock time to an integer UTC epoch."""
        if not isinstance(local_time, time):
            raise TypeError("local_time must be a datetime.time")
        if local_time.tzinfo is not None:
            raise ValueError("local_time must be a naive wall-clock time")
        if not isinstance(trade_date, date) or isinstance(trade_date, datetime):
            raise TypeError("trade_date must be a datetime.date")
        if exchange_timezone is not None and not isinstance(
            exchange_timezone, ZoneInfo
        ):
            raise TypeError("exchange_timezone must be zoneinfo.ZoneInfo or None")
        if unit not in {"s", "ms", "us", "ns"}:
            raise ValueError(f"unsupported timestamp unit: {unit}")

        resolved_timezone = exchange_timezone or ZoneInfo(cls.SHANGHAI_TIMEZONE_NAME)
        utc_datetime = datetime.combine(
            trade_date,
            local_time,
            tzinfo=resolved_timezone,
        ).astimezone(UTC)
        epoch_delta = utc_datetime - datetime(1970, 1, 1, tzinfo=UTC)
        total_microseconds = (
            epoch_delta.days * 86_400 + epoch_delta.seconds
        ) * 1_000_000 + epoch_delta.microseconds
        if unit == "s":
            return total_microseconds // 1_000_000
        if unit == "ms":
            return total_microseconds // 1_000
        if unit == "us":
            return total_microseconds
        return total_microseconds * 1_000

    @classmethod
    def combine_date_tick(
        cls,
        trade_date: date,
        tick_parts: tuple[int, int, int, int],
        timezone_info: ZoneInfo | None = None,
    ) -> datetime:
        """Combine a date and validated tick parts into a local aware datetime."""
        if not isinstance(trade_date, date) or isinstance(trade_date, datetime):
            raise TypeError("trade_date must be a datetime.date")
        if (
            not isinstance(tick_parts, tuple)
            or len(tick_parts) != _TICK_PART_COUNT
            or any(type(part) is not int for part in tick_parts)
        ):
            raise TypeError("tick_parts must be a four-integer tuple")
        if timezone_info is not None and not isinstance(timezone_info, ZoneInfo):
            raise TypeError("timezone_info must be zoneinfo.ZoneInfo or None")

        hour, minute, second, microsecond = tick_parts
        validated_time = time(hour, minute, second, microsecond)
        resolved_timezone = timezone_info or ZoneInfo(cls.SHANGHAI_TIMEZONE_NAME)
        return datetime.combine(
            trade_date,
            validated_time,
            tzinfo=resolved_timezone,
        )

    @classmethod
    def is_trading_time_utc(
        cls,
        timestamp: int | datetime,
        *,
        trading_sessions: Collection[TradingSession],
        market_timezone: ZoneInfo | None = None,
    ) -> bool:
        """Return whether a UTC instant falls within an injected local session."""
        if isinstance(trading_sessions, (str, bytes)) or not isinstance(
            trading_sessions, Collection
        ):
            raise TypeError("trading_sessions must be a collection")
        if any(
            not isinstance(trading_session, TradingSession)
            for trading_session in trading_sessions
        ):
            raise TypeError("trading_sessions must contain only TradingSession values")
        local_time = (
            cls.to_local(
                cls.parse_utc(timestamp),
                market_timezone,
            )
            .time()
            .replace(tzinfo=None)
        )
        return any(
            session.opens_at <= local_time <= session.closes_at
            for session in trading_sessions
        )

    @classmethod
    def is_trading_day_utc(
        cls,
        timestamp: int | datetime,
        *,
        trading_days: Collection[date],
        market_timezone: ZoneInfo | None = None,
    ) -> bool:
        """Return whether a UTC instant maps to an injected local trading date."""
        if isinstance(trading_days, (str, bytes)) or not isinstance(
            trading_days, Collection
        ):
            raise TypeError("trading_days must be a collection")
        if any(
            not isinstance(trading_day, date) or isinstance(trading_day, datetime)
            for trading_day in trading_days
        ):
            raise TypeError("trading_days must contain only datetime.date values")
        local_date = cls.to_local(cls.parse_utc(timestamp), market_timezone).date()
        return local_date in trading_days

    @classmethod
    def add_minutes_utc(
        cls,
        timestamp: int | datetime,
        minutes: int,
    ) -> datetime:
        """Add an integer number of minutes to a UTC instant."""
        if type(minutes) is not int:
            raise TypeError("minutes must be an int")
        return cls.parse_utc(timestamp) + timedelta(minutes=minutes)

    @classmethod
    def now_utc(cls) -> datetime:
        """Read the current aware UTC datetime at this explicit clock boundary."""
        return datetime.now(UTC)

    @classmethod
    def now(cls) -> datetime:
        """Read the current aware Shanghai datetime at this explicit boundary."""
        return cls.now_utc().astimezone(ZoneInfo(cls.SHANGHAI_TIMEZONE_NAME))

    @classmethod
    def today(cls) -> str:
        """Read the current Shanghai calendar date in system format."""
        return cls.now().date().isoformat()

    @classmethod
    def date_range(cls, start: str, end: str) -> list[str]:
        """Return an inclusive sequence of system dates from start through end."""
        start_value = cls.require_system_date(start, field_name="start")
        end_value = cls.require_system_date(end, field_name="end")
        current_date = date.fromisoformat(start_value)
        end_date = date.fromisoformat(end_value)
        if current_date > end_date:
            raise ValueError(
                f"invalid date range: start={start_value}, end={end_value}"
            )

        date_values: list[str] = []
        while current_date <= end_date:
            date_values.append(current_date.isoformat())
            current_date += timedelta(days=1)
        return date_values


__all__ = ["DateTimeUtils", "TradingSession"]
