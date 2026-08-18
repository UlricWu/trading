# filepath: src/training/steps/report.py
"""Read persisted training artifacts and publish the final HTML report."""

from __future__ import annotations

from src import logs
from src.observability.log_format import format_log_json
from src.training.artifact import load_training_report_inputs
from src.training.context import TrainingContext
from src.training.engines.report import build_training_report
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


class ReportStep:
    """Generate the final training report for one experiment.

    Example:
        step = ReportStep(
            pm=path_manager,
            experiment_name="training_2026-07-01_2026-07-20_run-1",
        )
        reported_context = step.run(persisted_context)
    """

    def __init__(self, *, pm: PathManager, experiment_name: str) -> None:
        """Bind the persisted experiment used as report input.

        Example:
            step = ReportStep(
                pm=path_manager,
                experiment_name="training_2026-07-01_2026-07-20_run-1",
            )
        """
        self._pm = pm
        self._experiment_name = experiment_name

    def run(self, context: TrainingContext) -> TrainingContext:
        """Publish the report and preserve the final training Context.

        Example:
            reported_context = step.run(persisted_context)
        """
        params, metrics = load_training_report_inputs(
            pm=self._pm,
            experiment_name=self._experiment_name,
        )
        report = build_training_report(
            experiment_name=self._experiment_name,
            params=params,
            metrics=metrics,
        )
        report_path = self._pm.experiment_training_report(
            experiment_name=self._experiment_name,
        )
        FileSystem.write_bytes_atomic(report_path, report.html.encode("utf-8"))
        logs.info(
            f"saved path={report_path}\n"
            f"{format_log_json('rank_ic_summary', report.summary)}"
        )
        return context
