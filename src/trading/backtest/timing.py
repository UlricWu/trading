# filepath: src/trading/backtest/timing.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.utils.datetime_utils import DateTimeUtils

__all__ = ("BacktestTiming", "resolve_backtest_timing")


@dataclass(frozen=True, slots=True)
class BacktestTiming:
    """Identify one signal date and its forward evaluation date.

    Example:
        timing = BacktestTiming(
            signal_date="2026-07-20",
            forward_date="2026-07-21",
        )
    """

    signal_date: str
    forward_date: str

    def __post_init__(self) -> None:
        for field_name in ("signal_date", "forward_date"):
            DateTimeUtils.require_system_date(
                getattr(self, field_name),
                field_name=field_name,
            )


def resolve_backtest_timing(
    *,
    open_dates: Sequence[str],
) -> list[BacktestTiming]:
    """Resolve ordered open dates into daily-alpha timing rows.

    Example:
        timings = resolve_backtest_timing(
            open_dates=("2026-07-20", "2026-07-21"),
        )
    """
    dates = [
        DateTimeUtils.require_system_date(open_date, field_name="open_date")
        for open_date in open_dates
    ]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("open_dates must be unique and sorted ascending")
    return [
        BacktestTiming(
            signal_date=dates[i],
            forward_date=dates[i + 1],
        )
        for i in range(max(0, len(dates) - 1))
    ]
