# filepath: src/trading/backtest/pipeline.py
from __future__ import annotations

from collections.abc import Callable, Sequence
from time import monotonic

from src import logs

from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.market.replay import ReplaySession
from src.trading.backtest.timing import BacktestTiming


class BacktestPipeline:
    """Advance one account clock with explicit, ordered workflow-supplied Steps.

    Example:
        pipeline = BacktestPipeline(clock=clock, session_at=market.session_at,
            per_timing_steps=steps, final_steps=(), instrumentation=instrumentation)
        pipeline.run(state)
    """

    def __init__(self, *, clock: ReplayClock, session_at: Callable[[int], ReplaySession],
                 per_timing_steps: Sequence[PipelineStep[BacktestContext]],
                 final_steps: Sequence[PipelineStep[BacktestContext]],
                 instrumentation: Instrumentation, progress_clock: Callable[[], float] = monotonic) -> None:
        """Bind the dynamic schedule without consuming or freezing it.

        Example:
            pipeline = BacktestPipeline(clock=clock, session_at=market.session_at,
                per_timing_steps=steps, final_steps=(), instrumentation=instrumentation)
        """
        self._clock = clock
        self._session_at = session_at
        self._steps = tuple(per_timing_steps)
        self._final_steps = tuple(final_steps)
        self._instrumentation = instrumentation
        self._progress_clock = progress_clock

    def run(self, state: BacktestState) -> None:
        """Preserve stage order, fresh Contexts and deadlines discovered during replay.

        Example:
            pipeline.run(state)
        """
        context: BacktestContext | None = None
        last_progress = self._progress_clock()
        with self._instrumentation:
            for count, ts_us in enumerate(self._clock, 1):
                session = self._session_at(ts_us)
                try:
                    context = run_steps(context=BacktestContext(
                        BacktestTiming(ts_us, session.trade_date), state),
                        steps=self._steps, instrumentation=self._instrumentation)
                except Exception as exc:
                    exc.add_note(f"回测时点; trade_date={session.trade_date} ts_us={ts_us}")
                    raise
                state = context.state
                for wakeup in sorted(context.requested_wakeup_ts_us):
                    self._clock.schedule(ts_us=wakeup)
                now = self._progress_clock()
                if now - last_progress >= 5 and len(self._clock):
                    logs.info(f"⏳ 回测推进; trade_date={session.trade_date} ts_us={ts_us} processed={count}")
                    last_progress = now
            if context is None:
                raise ValueError("backtest requires at least one clock tick")
            run_steps(context=context, steps=self._final_steps, instrumentation=self._instrumentation)
