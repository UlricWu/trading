# filepath: tests/trading/backtest/test_pipeline.py
"""Timing scheduling tests for the daily-alpha Backtest Pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Self, cast
from unittest.mock import Mock

import pytest

from src import logs
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
        clock=lambda: 0.0,
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


@pytest.mark.parametrize(
    ("times", "progress_dates"),
    [
        ([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6], []),
        ([0.0, 1.0, 4.99, 5.0, 9.99, 10.0, 15.0], ["2026-07-03", "2026-07-05"]),
    ],
)
def test_progress_is_throttled_and_omits_final_date(
    tmp_path: Path, times: list[float], progress_dates: list[str],
) -> None:
    output = tmp_path / "progress.log"
    sink = logs.add(output, format="{message}", level="INFO", filter="src.trading.backtest.pipeline")
    pipeline = BacktestPipeline(
        timings=tuple(
            BacktestTiming(signal_date=f"2026-07-{day:02d}", forward_date=f"2026-07-{day + 1:02d}")
            for day in range(1, 7)
        ),
        per_timing_steps=(_FirstStep([], []),), final_steps=(),
        instrumentation=cast("Instrumentation", _Instrumentation()), clock=Mock(side_effect=times),
    )
    try:
        pipeline.run(BacktestState.initial(initial_cash=200_000))
    finally:
        logs.remove(sink)

    messages = output.read_text().splitlines()
    assert len(messages) == len(progress_dates)
    for message, day in zip(messages, progress_dates, strict=True):
        assert message.startswith(f"⏳ 回测推进; trade_date={day} ")
        assert f"progress={int(day[-2:])}/6" in message


def test_failed_timing_keeps_original_error_and_adds_dates() -> None:
    error = RuntimeError("source unavailable")
    step = Mock()
    step.run.side_effect = error
    final = Mock()
    pipeline = BacktestPipeline(
        timings=(BacktestTiming(signal_date="2026-07-03", forward_date="2026-07-06"),),
        per_timing_steps=(step,), final_steps=(final,),
        instrumentation=cast("Instrumentation", _Instrumentation()), clock=lambda: 0.0,
    )

    with pytest.raises(RuntimeError, match="source unavailable") as raised:
        pipeline.run(BacktestState.initial(initial_cash=200_000))

    assert raised.value is error
    assert error.__notes__ == ["回测日期; signal_date=2026-07-03 forward_date=2026-07-06"]
    final.run.assert_not_called()
