# filepath: src/trading/backtest/steps/report.py
from __future__ import annotations

from src.trading.backtest.context import TradingContext
from src.trading.reporting.bundle import ReportingBundle


class ReportStep:
    """Generate the formal backtest report from experiment artifacts.

    This step only passes `ctx.pm` and `ctx.experiment_name` into
    `ReportingBundle`; it does not read runtime result objects from context.

    Example:
        step = ReportStep()
        step(context)
    """

    def __call__(self, ctx: TradingContext) -> None:
        """Generate the final backtest report.

        Example:
            step(context)
        """
        ReportingBundle(pm=ctx.pm).run(experiment_name=ctx.experiment_name)
