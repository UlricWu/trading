# filepath: src/trading/core/time.py
from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from collections.abc import Iterable, Iterator, Sequence
from datetime import date, time
from heapq import heapify, heappop, heappush
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from src.utils.datetime_utils import DateTimeUtils

if TYPE_CHECKING:
    from src.trading.market.data_view import MarketDataView

US_PER_SECOND = 1_000_000
US_PER_MINUTE = 60 * US_PER_SECOND


@dataclass(slots=True)
class ReplayClock:
    """Merge known timestamps and dynamic deadlines into one increasing clock.

    Each clock belongs to one run and is consumed once. Scheduling only indexes
    deadlines; it neither retains trading intent nor produces market data.

    Example:
        clock = ReplayClock.from_iterable((1, 3))
        for ts in clock:
            if ts == 1:
                clock.schedule(ts_us=2)
    """

    _timestamps: InitVar[Sequence[int]]
    _end_us: int | None = None
    _queue: list[int] = field(init=False, repr=False)
    _scheduled: set[int] = field(init=False, repr=False)
    _current: int | None = field(default=None, init=False)

    def __post_init__(self, _timestamps: Sequence[int]) -> None:
        self._scheduled = set(_timestamps)
        self._queue = list(self._scheduled)
        heapify(self._queue)

    # ============================================================
    # Iteration
    # ============================================================

    def __iter__(self) -> Iterator[int]:
        while self._queue:
            self._current = heappop(self._queue)
            self._scheduled.remove(self._current)
            yield self._current

    def schedule(self, *, ts_us: int) -> None:
        """Add a future wakeup, merging duplicates with known input times.

        Example:
            clock.schedule(ts_us=exit_due_ts_us)
        """
        if self._current is not None and ts_us <= self._current:
            raise ValueError("scheduled timestamp must be strictly after current time")
        if self._end_us is not None and ts_us > self._end_us:
            return
        if ts_us not in self._scheduled:
            heappush(self._queue, ts_us)
            self._scheduled.add(ts_us)

    def __len__(self) -> int:
        return len(self._queue)

    def __getitem__(self, idx: int) -> int:
        return sorted(self._queue)[idx]

    # ============================================================
    # Constructors
    # ============================================================

    @classmethod
    def from_iterable(
            cls,
            timestamps: Iterable[int],
            *,
            start_us: int | None = None,
            end_us: int | None = None,
    ) -> "ReplayClock":
        """
        Build clock from flat iterable.

        Automatically:
        - casts to int
        - sorts ascending
        - removes duplicates
        - optionally clips by [start_us, end_us]

        Example:
            clock = ReplayClock.from_iterable((1, 3, 5), end_us=5)
            clock.schedule(ts_us=2)
        """

        ts = sorted({int(x) for x in timestamps})

        if start_us is not None:
            start_us = int(start_us)
            ts = [x for x in ts if x >= start_us]

        if end_us is not None:
            end_us = int(end_us)
            ts = [x for x in ts if x <= end_us]

        return cls(tuple(ts), _end_us=end_us)

    # ------------------------------------------------------------

    @classmethod
    def from_symbol_ts(
            cls,
            symbol_ts: Sequence[Sequence[int]],
            *,
            start_us: int | None = None,
            end_us: int | None = None,
    ) -> "ReplayClock":
        """
        Merge timestamps from multiple symbols.

        Example:
        --------
        symbol_ts = [
            [t1, t2, t3],
            [t2, t3, t4],
        ]

        Output:
            sorted unique union of all timestamps

        Guarantees:
        - no duplicates
        - ascending
        - clipped to range if provided
        """

        merged = set()

        for seq in symbol_ts:
            for x in seq:
                merged.add(int(x))

        return cls.from_iterable(
            merged,
            start_us=start_us,
            end_us=end_us,
        )

    # ------------------------------------------------------------

    @classmethod
    def from_data_view(
            cls,
            data_view: MarketDataView,
            *,
            start_us: int | None = None,
            end_us: int | None = None,
    ) -> "ReplayClock":
        """
        Build directly from a MarketDataView.

        Requires data_view to implement:
            bar_timestamps_us() -> list[int]

        This normalizes the replay bar timestamp axis only. Raw event
        ordering, duplicate data rows, and same-timestamp Level-2 events must
        be resolved by the DataView or its upstream data construction.
        """

        ts = data_view.bar_timestamps_us()

        return cls.from_iterable(
            ts,
            start_us=start_us,
            end_us=end_us,
        )

    # ============================================================
    # Utilities
    # ============================================================

    def first(self) -> int:
        """Return the earliest pending wakeup.

        Example:
            first_ts = clock.first()
        """
        if not self._queue:
            raise RuntimeError("ReplayClock empty")
        return self._queue[0]

    def last(self) -> int:
        """Return the latest pending wakeup.

        Example:
            last_ts = clock.last()
        """
        if not self._queue:
            raise RuntimeError("ReplayClock empty")
        return max(self._queue)

    def to_list(self) -> list[int]:
        """Return an independent ordered snapshot of pending wakeups.

        Example:
            pending_times = clock.to_list()
        """
        return sorted(self._queue)


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
