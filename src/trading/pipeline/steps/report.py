# filepath: src/trading/pipeline/steps/report.py
from __future__ import annotations

from src.pipeline.step import PipelineStep
from src.trading.pipeline.context import TradingContext
from src.trading.reporting.bundle import ReportingBundle


class ReportStep(PipelineStep[TradingContext]):
    """
    Generate the formal backtest report from experiment artifacts.

    This step only passes `ctx.pm` and `ctx.experiment_name` into
    `ReportingBundle`; it does not read runtime result objects from context.
    """

    stage = "report"

    def run(self, ctx: TradingContext) -> TradingContext:
        ReportingBundle(pm=ctx.pm).run(experiment_name=ctx.experiment_name)
        return ctx
