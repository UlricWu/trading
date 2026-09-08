# filepath: tests/trading/backtest/test_timing.py
"""Date-relation tests for daily-alpha backtest timing."""

from __future__ import annotations

from src.trading.backtest.timing import resolve_backtest_timing


def test_timing_pairs_adjacent_open_dates() -> None:
    timings = resolve_backtest_timing(
        open_dates=("2026-07-01", "2026-07-02", "2026-07-03")
    )

    assert [(timing.signal_date, timing.forward_date) for timing in timings] == [
        ("2026-07-01", "2026-07-02"),
        ("2026-07-02", "2026-07-03"),
    ]
