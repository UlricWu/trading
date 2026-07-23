# filepath: src/trading/pipeline/pipeline.py
from __future__ import annotations

from collections.abc import Sequence

from src.observability.instrumentation import Instrumentation, NoOpInstrumentation
from src.pipeline.step import PipelineStep
from src.trading.backtest.timing import BacktestTiming
from src.trading.pipeline.context import TradingContext


class TradingPipeline:
    """Run the fixed per-timing graph, then the final artifact graph."""

    def __init__(
            self,
            *,
            backtest_timings: Sequence[BacktestTiming],
            per_timing_steps: Sequence[PipelineStep[TradingContext]],
            final_steps: Sequence[PipelineStep[TradingContext]] = (),
            inst: Instrumentation | NoOpInstrumentation | None = None,
    ) -> None:
        self.backtest_timings = tuple(backtest_timings)
        self.per_timing_steps = tuple(per_timing_steps)
        self.final_steps = tuple(final_steps)
        self.inst = inst if inst is not None else NoOpInstrumentation()

    def run(
            self,
            ctx: TradingContext,
    ) -> TradingContext:
        if not self.backtest_timings:
            raise RuntimeError("[TradingPipeline] backtest_timings is required")

        for timing in self.backtest_timings:
            ctx.backtest_timing = timing
            for step in self.per_timing_steps:
                with self.inst.timer(step.__class__.__name__):
                    next_ctx = step.run(ctx)
                if next_ctx is None:
                    raise RuntimeError(
                        f"backtest step returned no context: {step.__class__.__name__}"
                    )
                ctx = next_ctx

        for step in self.final_steps:
            with self.inst.timer(step.__class__.__name__):
                next_ctx = step.run(ctx)
            if next_ctx is None:
                raise RuntimeError(
                    f"backtest step returned no context: {step.__class__.__name__}"
                )
            ctx = next_ctx

        self.inst.generate_timeline_report(ctx.experiment_name)

        return ctx
