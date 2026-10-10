# filepath: src/trading/backtest/pipeline.py
"""Schedule workflow-supplied Steps over daily-alpha backtest timings."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

from src import logs
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.timing import BacktestTiming


class BacktestPipeline:
    """Run ordered Step tuples over an already resolved timing schedule.

    Example:
        pipeline = BacktestPipeline(
            timings=timings,
            per_timing_steps=(signal, evaluate, portfolio, execute),
            final_steps=(persist, report),
            instrumentation=Instrumentation("backtest_2026-07_run-1"),
            clock=monotonic,
        )
        pipeline.run(BacktestState.initial(initial_cash=200_000))
    """

    def __init__(
        self,
        *,
        timings: Sequence[BacktestTiming],
        per_timing_steps: Sequence[PipelineStep[BacktestContext]],
        final_steps: Sequence[PipelineStep[BacktestContext]],
        instrumentation: Instrumentation,
        clock: Callable[[], float],
    ) -> None:
        """Preserve the workflow-supplied schedule and Step order.

        Example:
            pipeline = BacktestPipeline(
                timings=timings,
                per_timing_steps=(signal, evaluate, portfolio, execute),
                final_steps=(persist, report),
                instrumentation=Instrumentation("backtest_2026-07_run-1"),
                clock=monotonic,
            )
        """
        self._timings = tuple(timings)
        self._per_timing_steps = tuple(per_timing_steps)
        self._final_steps = tuple(final_steps)
        self._instrumentation = instrumentation
        self._clock = clock

    def run(self, state: BacktestState) -> None:
        """Execute every timing and then the final Steps in supplied order.

        Example:
            pipeline.run(BacktestState.initial(initial_cash=200_000))
        """
        if not self._timings:
            raise ValueError("[BacktestWorkflow] backtest timings are required")

        last_context: BacktestContext | None = None
        with self._instrumentation:
            last_progress = self._clock()
            days = len(self._timings)
            for completed, timing in enumerate(self._timings, start=1):
                try:
                    last_context = run_steps(
                        context=BacktestContext(timing=timing, state=state),
                        steps=self._per_timing_steps,
                        instrumentation=self._instrumentation,
                    )
                except Exception as exc:
                    exc.add_note(
                        f"回测日期; signal_date={timing.signal_date} forward_date={timing.forward_date}"
                    )
                    raise
                state = last_context.state
                now = self._clock()
                if completed < days and now - last_progress >= 5.0:
                    logs.info(
                        f"⏳ 回测推进; trade_date={timing.signal_date} progress={completed}/{days} "
                        f"signals={state.signal_count:,} positions={len(state.portfolio_state.positions):,}"
                    )
                    last_progress = now

            final_context = cast(BacktestContext, last_context)
            run_steps(
                context=final_context,
                steps=self._final_steps,
                instrumentation=self._instrumentation,
            )
