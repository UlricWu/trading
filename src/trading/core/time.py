# filepath: src/trading/core/time.py
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Iterator, Sequence

from src.trading.market.data_view import MarketDataView

US_PER_SECOND = 1_000_000
US_PER_MINUTE = 60 * US_PER_SECOND


@dataclass(frozen=True, slots=True)
class ReplayClock:
    """Immutable, bar-driven replay clock with O(N) iteration.

    Contract
    --------
    - Iterates strictly over provided timestamps
    - Does NOT synthesize time
    - Does NOT assume fixed frequency
    - No second-level stepping

    Complexity
    ----------
    O(N bars)

    Usage
    -----
    clock = ReplayClock.from_symbol_ts(list_of_lists)
    for ts in clock:
        ...
    """

    _timestamps: tuple[int, ...]

    # ============================================================
    # Iteration
    # ============================================================

    def __iter__(self) -> Iterator[int]:
        return iter(self._timestamps)

    def __len__(self) -> int:
        return len(self._timestamps)

    def __getitem__(self, idx: int) -> int:
        return self._timestamps[idx]

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
        """

        ts = sorted({int(x) for x in timestamps})

        if start_us is not None:
            start_us = int(start_us)
            ts = [x for x in ts if x >= start_us]

        if end_us is not None:
            end_us = int(end_us)
            ts = [x for x in ts if x <= end_us]

        return cls(tuple(ts))

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
        if not self._timestamps:
            raise RuntimeError("ReplayClock empty")
        return self._timestamps[0]

    def last(self) -> int:
        if not self._timestamps:
            raise RuntimeError("ReplayClock empty")
        return self._timestamps[-1]

    def to_list(self) -> list[int]:
        return list(self._timestamps)


def is_minute_boundary(ts_us: int) -> bool:
    return int(ts_us) % US_PER_MINUTE == 0
