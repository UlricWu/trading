# filepath: src/training/steps/ic_evaluate.py
"""Rank-IC evaluation for one explicit model and evaluation partition."""

from src.training.context import TrainingContext
from src.training.engines.rank_ic import evaluate_rank_ic


class ICEvaluateStep:
    """Evaluate and record Rank IC for one trained Context window.

    Example:
        evaluated_context = ICEvaluateStep().run(trained_context)
    """

    def run(self, context: TrainingContext) -> TrainingContext:
        """Attach Rank IC and record it under the Context evaluation date.

        Example:
            evaluated_context = ICEvaluateStep().run(trained_context)
        """
        if context.model is None or context.eval_X is None or context.eval_y is None:
            raise RuntimeError("ICEvaluateStep requires a model and evaluation data")
        rank_ic = evaluate_rank_ic(
            model=context.model,
            eval_X=context.eval_X,
            eval_y=context.eval_y,
        )
        context.metrics[f"ic@{context.window.eval_date}"] = rank_ic
        return context
