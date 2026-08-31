# filepath: src/trading/backtest/timing.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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
    dates = tuple(open_dates)
    return [
        BacktestTiming(
            signal_date=dates[i],
            forward_date=dates[i + 1],
        )
        for i in range(len(dates) - 1)
    ]
