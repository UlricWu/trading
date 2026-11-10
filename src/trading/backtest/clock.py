# filepath: src/trading/backtest/clock.py
from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import InitVar, dataclass, field
from heapq import heapify, heappop, heappush

from src.trading.backtest.market.data_view import MarketDataView


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
