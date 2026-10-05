# filepath: src/training/steps/ic_evaluate.py
"""Read corresponding labels only after the prediction record is fixed."""

from __future__ import annotations

from src.training.context import TrainingContext
from src.training.evaluation import evaluate_prediction_record
from src.utils.path import PathManager


class ICEvaluateStep:
    """Append one observation for the saved model and its complete prediction rows.

    Example:
        step = ICEvaluateStep(pm=path_manager)
        context = step.run(predicted_context)
    """

    def __init__(self, *, pm: PathManager) -> None:
        """Bind label storage without reading it during composition.

        Example:
            step = ICEvaluateStep(pm=path_manager)
        """
        self._pm = pm

    def run(self, context: TrainingContext) -> TrainingContext:
        """Evaluate exact key-aligned labels using the model's actual target.

        Example:
            context = step.run(predicted_context)
        """
        window = context.window
        if window.eval_date is None:
            return context
        if window.prediction_ref is None:
            raise RuntimeError("ICEvaluateStep requires a persisted prediction ref")
        observation = evaluate_prediction_record(window.prediction_ref, pm=self._pm)
        context.observations.append(observation)
        return context
