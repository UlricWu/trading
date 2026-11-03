# filepath: src/trading/backtest/pipeline.py
from __future__ import annotations

from collections.abc import Callable, Sequence
from time import monotonic
from typing import cast

from src import logs
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.context import BacktestContext, BacktestInputs, TickState
from src.trading.backtest.reporting.metrics import BacktestMetrics


class BacktestPipeline:
    """Execute startup, per-tick and result Steps within one measured replay.

    Example:
        pipeline = BacktestPipeline(startup_steps=startup, tick_steps=ticks,
            result_steps=results, instrumentation=instrumentation)
        result = pipeline.run(metrics=metrics)
    """

    def __init__(
        self, *, startup_steps: Sequence[PipelineStep[BacktestContext]],
        tick_steps: Sequence[PipelineStep[BacktestContext]],
        result_steps: Sequence[PipelineStep[BacktestContext]],
        instrumentation: Instrumentation, progress_clock: Callable[[], float] = monotonic,
    ) -> None:
        """Own each ordered group without selecting, discovering or reordering Steps.

        Example:
            pipeline = BacktestPipeline(startup_steps=startup, tick_steps=ticks,
                result_steps=results, instrumentation=instrumentation, progress_clock=lambda: 0)
        """
        self._startup_steps = tuple(startup_steps)
        self._tick_steps = tuple(tick_steps)
        self._result_steps = tuple(result_steps)
        self._instrumentation = instrumentation
        self._progress_clock = progress_clock

    def run(self, *, metrics: BacktestMetrics) -> BacktestContext:
        """Create fresh run and tick state, preserving every Step's returned Context.

        Example:
            result = pipeline.run(metrics=metrics)
            portfolio = result.metrics.portfolio
        """
        context = BacktestContext(metrics=metrics)
        with self._instrumentation:
            context = run_steps(context=context, steps=self._startup_steps, instrumentation=self._instrumentation)
            clock = cast(ReplayClock, context.clock)
            last_progress = self._progress_clock()
            for count, ts_us in enumerate(clock, 1):
                session = cast(BacktestInputs, context.inputs).market.session_at(ts_us)
                context.tick = TickState(ts_us=ts_us, session=session)
                try:
                    context = run_steps(context=context, steps=self._tick_steps, instrumentation=self._instrumentation)
                except Exception as exc:
                    exc.add_note(f"回测时点; trade_date={session.trade_date} ts_us={ts_us}")
                    raise
                now = self._progress_clock()
                if now - last_progress >= 5 and len(clock):
                    logs.info(f"⏳ 回测推进; trade_date={session.trade_date} ts_us={ts_us} processed={count}")
                    last_progress = now
            context.tick = None
            return run_steps(context=context, steps=self._result_steps, instrumentation=self._instrumentation)
