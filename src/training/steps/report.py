# filepath: src/training/steps/report.py
"""Read persisted training artifacts and publish the final HTML report."""

from __future__ import annotations

import json
from pathlib import Path

from src import logs
from src.observability.log_format import format_log_json
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
        params = _load_json_object(
            self._pm.experiment_training_params(
                experiment_name=self._experiment_name,
            ),
            label="training params",
        )
        metrics = _load_json_object(
            self._pm.experiment_training_metrics(
                experiment_name=self._experiment_name,
            ),
            label="training metrics",
        )
        report = build_training_report(
            experiment_name=self._experiment_name,
            params_payload=params,
            metrics_payload=metrics,
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


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be a JSON object")
    return {str(key): value for key, value in payload.items()}
