# filepath: tests/trading/backtest/test_clock.py
import pytest

from src.trading.backtest.clock import ReplayClock


def test_clock_merges_dynamic_deadlines_once_without_synthetic_bars() -> None:
    clock = ReplayClock.from_iterable((1, 3, 3, 5))
    visited: list[int] = []
    for ts in clock:
        visited.append(ts)
        if ts == 1:
            clock.schedule(ts_us=2)
            clock.schedule(ts_us=3)
            clock.schedule(ts_us=2)
        if ts == 3:
            clock.schedule(ts_us=4)
    assert visited == [1, 2, 3, 4, 5]
    with pytest.raises(ValueError, match="strictly after"):
        clock.schedule(ts_us=5)


def test_dynamic_deadline_cannot_extend_the_explicit_simulation_endpoint() -> None:
    clock = ReplayClock.from_iterable((1, 3, 7), end_us=3)
    visited: list[int] = []
    for ts in clock:
        visited.append(ts)
        if ts == 1:
            clock.schedule(ts_us=2)
            clock.schedule(ts_us=4)
    assert visited == [1, 2, 3]


def test_clock_queries_include_dynamic_pending_deadlines() -> None:
    clock = ReplayClock.from_iterable((1, 3))
    clock.schedule(ts_us=2)
    assert clock.to_list() == [1, 2, 3]
    assert len(clock) == 3
    assert clock.first() == 1
    assert clock.last() == 3
    iterator = iter(clock)
    assert next(iterator) == 1
    assert clock.to_list() == [2, 3]
