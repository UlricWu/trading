# filepath: src/training/steps/model_evaluate_step.py
"""Rank-IC evaluation for one explicit model and evaluation partition."""

from __future__ import annotations

import pandas as pd
from scipy.stats import spearmanr

from src.training.context import TrainingContext
from src.training.inference_model import PredictionModel
from src.utils import table_ops


def evaluate_rank_ic(
    *,
    model: PredictionModel,
    eval_X: pd.DataFrame,
    eval_y: pd.Series,
) -> float:
    """Return Rank IC without mutating workflow-owned metrics.

    Example:
        rank_ic = evaluate_rank_ic(
            model=model,
            eval_X=eval_X,
            eval_y=eval_y,
        )
    """
    table_ops.require_nonempty(eval_X, who="ICEvaluateStep eval_X")
    if len(eval_X) != len(eval_y):
        raise RuntimeError("[ICEvaluateStep] eval_X / eval_y length mismatch")
    if not eval_X.index.equals(eval_y.index):
        raise RuntimeError("[ICEvaluateStep] eval_X / eval_y index mismatch")
    predictions = model.predict(eval_X.values)
    rank_ic, _ = spearmanr(predictions, eval_y.values)
    return float(rank_ic)


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
        context.rank_ic = evaluate_rank_ic(
            model=context.model,
            eval_X=context.eval_X,
            eval_y=context.eval_y,
        )
        context.metrics[f"ic@{context.window.eval_date}"] = context.rank_ic
        return context
