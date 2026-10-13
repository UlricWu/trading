# filepath: src/trading/backtest/steps/report.py
from __future__ import annotations

from dataclasses import dataclass

from src.trading.backtest.context import BacktestResultContext
from src.trading.backtest.reporting.bundle import ReportingBundle


@dataclass(frozen=True, slots=True)
class ReportStep:
    """Render the persisted results before completing the workflow's measured scope.

    Example:
        context = ReportStep(ReportingBundle(output_dir)).run(context)
    """

    bundle: ReportingBundle

    def run(self, context: BacktestResultContext) -> BacktestResultContext:
        """Publish HTML and its concise summary, retaining report failures as failures.

        Example:
            context = step.run(context)
        """
        self.bundle.run()
        return context
