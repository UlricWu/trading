# filepath: src/trading/backtest/steps/report.py
from __future__ import annotations

from dataclasses import dataclass

from src.trading.backtest.context import BacktestResultContext, RiskEvaluationContext, SignalEvaluationContext
from src.trading.backtest.reporting.bundle import ReportingBundle


@dataclass(frozen=True, slots=True)
class ReportStep:
    """Render the persisted results before completing the workflow's measured scope.

    Example:
        context = ReportStep(ReportingBundle(pm), experiment_name).run(context)
    """

    bundle: ReportingBundle
    experiment_name: str

    def run[ContextT: SignalEvaluationContext | RiskEvaluationContext | BacktestResultContext](
        self, context: ContextT,
    ) -> ContextT:
        """Publish HTML and its concise summary, retaining report failures as failures.

        Example:
            context = step.run(context)
        """
        self.bundle.run(experiment_name=self.experiment_name)
        return context
