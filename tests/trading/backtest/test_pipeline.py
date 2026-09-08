# filepath: tests/trading/backtest/test_pipeline.py
"""Timing scheduling tests for the daily-alpha Backtest Pipeline."""

from __future__ import annotations

from typing import Self, cast

from src.observability.instrumentation import Instrumentation
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.timing import BacktestTiming


class _Instrumentation:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def measure(
        self,
        operation_name: str,
        operation: object,
        *args: object,
    ) -> object:
        return cast("object", operation)(*args)


class _FirstStep:
    def __init__(self, events: list[tuple[str, str]], context_ids: list[int]) -> None:
        self._events = events
        self._context_ids = context_ids

    def run(self, context: BacktestContext) -> BacktestContext:
        self._events.append(("first", context.timing.signal_date))
        self._context_ids.append(id(context))
        context.state.bar_count += 1
        return context


class _SecondStep:
    def __init__(self, events: list[tuple[str, str]]) -> None:
        self._events = events

    def run(self, context: BacktestContext) -> BacktestContext:
        self._events.append(("second", context.timing.signal_date))
        return context


class _FinalStep:
    def __init__(
        self, events: list[tuple[str, str]], states: list[BacktestState]
    ) -> None:
        self._events = events
        self._states = states

    def run(self, context: BacktestContext) -> BacktestContext:
        self._events.append(("final", context.timing.signal_date))
        self._states.append(context.state)
        return context


def test_backtest_pipeline_uses_fresh_timing_contexts_and_shared_state() -> None:
    events: list[tuple[str, str]] = []
    context_ids: list[int] = []
    final_states: list[BacktestState] = []
    state = BacktestState.initial(initial_cash=200_000)
    pipeline = BacktestPipeline(
        timings=(
            BacktestTiming(
                signal_date="2026-07-01",
                forward_date="2026-07-02",
            ),
            BacktestTiming(
                signal_date="2026-07-02",
                forward_date="2026-07-03",
            ),
        ),
        per_timing_steps=(
            _SecondStep(events),
            _FirstStep(events, context_ids),
        ),
        final_steps=(_FinalStep(events, final_states),),
        instrumentation=cast("Instrumentation", _Instrumentation()),
    )

    pipeline.run(state)

    assert events == [
        ("second", "2026-07-01"),
        ("first", "2026-07-01"),
        ("second", "2026-07-02"),
        ("first", "2026-07-02"),
        ("final", "2026-07-02"),
    ]
    assert len(set(context_ids)) == 2
    assert state.bar_count == 2
    assert final_states == [state]
