# filepath: src/trading/backtest/timing.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BacktestTiming:
    """The current microsecond and explicitly resolved account trading day.

    Example:
        timing = BacktestTiming(ts_us=decision_us, trade_date="2026-07-03")
    """

    ts_us: int
    trade_date: str
