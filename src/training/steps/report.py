# filepath: src/training/steps/report.py
"""Read independent persisted models and metrics to publish a training report."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from src import logs
from src.training.artifact import load
from src.training.context import TrainingContext
from src.training.engines.report import build_training_report
from src.training.evaluation import load_evaluation
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


class ReportStep:
    """Report all explicit refs, without treating the last model as the experiment.

    Example:
        step = ReportStep(pm=path_manager, experiment_name="training_run", model_refs=(model_ref,))
        context = step.run(evaluated_context)
    """

    def __init__(
        self, *, pm: PathManager, experiment_name: str, model_refs: Sequence[Path],
    ) -> None:
        """Bind persisted inputs and preserve the workflow's reference order.

        Example:
            step = ReportStep(pm=path_manager, experiment_name="training_run", model_refs=(model_ref,))
        """
        self._pm = pm
        self._experiment_name = experiment_name
        self._model_refs = tuple(model_refs)

    def run(self, context: TrainingContext) -> TrainingContext:
        """Publish HTML independently of the last Context's model state.

        Example:
            context = step.run(evaluated_context)
        """
        evaluation = load_evaluation(self._pm.experiment_training_metrics(experiment_name=self._experiment_name))
        html = build_training_report(
            experiment_name=self._experiment_name,
            models={ref: load(ref) for ref in self._model_refs}, evaluation=evaluation,
        )
        output = self._pm.experiment_training_report(experiment_name=self._experiment_name)
        FileSystem.write_bytes_atomic(output, html.encode("utf-8"))
        logs.info(f"✅ training report publish; path={output} defined_date_count={evaluation.summary.defined_date_count} date_count={evaluation.summary.date_count}")
        return context
