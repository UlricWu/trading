# filepath: src/trading/backtest/steps/report.py
"""Generate the formal backtest report from persisted artifacts."""

from __future__ import annotations

from src.trading.backtest.context import BacktestContext
from src.trading.reporting.bundle import ReportingBundle
from src.utils.path import PathManager


def generate_backtest_report(
    *,
    pm: PathManager,
    experiment_name: str,
) -> None:
    """Generate one formal backtest report.

    Example:
        generate_backtest_report(
            pm=path_manager,
            experiment_name="backtest_2026-07-01_2026-07-20_run-1",
        )
    """
    ReportingBundle(pm=pm).run(experiment_name=experiment_name)


class ReportStep:
    """Generate the final report for one persisted backtest experiment.

    Example:
        step = ReportStep(
            pm=path_manager,
            experiment_name="backtest_2026-07-01_2026-07-20_run-1",
        )
        reported_context = step.run(persisted_context)
    """

    def __init__(self, *, pm: PathManager, experiment_name: str) -> None:
        """Bind the persisted experiment used as report input.

        Example:
            step = ReportStep(
                pm=path_manager,
                experiment_name="backtest_2026-07-01_2026-07-20_run-1",
            )
        """
        self._pm = pm
        self._experiment_name = experiment_name

    def run(self, context: BacktestContext) -> BacktestContext:
        """Generate the report and preserve the final backtest Context.

        Example:
            reported_context = step.run(persisted_context)
        """
        generate_backtest_report(
            pm=self._pm,
            experiment_name=self._experiment_name,
        )
        return context
