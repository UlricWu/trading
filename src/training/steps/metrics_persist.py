# filepath: src/training/steps/metrics_persist.py
"""Publish aggregate observations independently of model delivery."""

from src.training.context import TrainingContext
from src.training.evaluation import save_evaluation
from src.utils.path import PathManager


class MetricsPersistStep:
    """Persist daily evaluation records and the interval's coverage summary.

    Example:
        step = MetricsPersistStep(pm=path_manager, experiment_name="training_run")
        context = step.run(evaluated_context)
    """

    def __init__(self, *, pm: PathManager, experiment_name: str) -> None:
        """Bind the experiment's metrics output.

        Example:
            step = MetricsPersistStep(pm=path_manager, experiment_name="training_run")
        """
        self._output = pm.experiment_training_metrics(experiment_name=experiment_name)

    def run(self, context: TrainingContext) -> TrainingContext:
        """Publish the shared observation collection without consuming a final model.

        Example:
            context = step.run(evaluated_context)
        """
        save_evaluation(self._output, context.observations)
        return context
