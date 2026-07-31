# filepath: src/training/steps/model_evaluate_step.py
from __future__ import annotations

from scipy.stats import spearmanr

from src.utils import table_ops
from src.training.context import TrainingContext


class ICEvaluateStep:
    """
    Evaluate the current model on one in-memory eval date.

    This is the only daily Rank IC evaluation boundary in offline training. It
    writes the scalar metric to `ctx.metrics[f"ic@{ctx.eval_date}"]`; it does
    not persist predictions or artifacts.

    Example:
        step = ICEvaluateStep()
        step(context)
    """

    def __call__(self, ctx: TrainingContext) -> None:
        """Record Rank IC for the current evaluation date.

        Example:
            step(context)
        """
        table_ops.require_nonempty(ctx.eval_X, who="ICEvaluateStep eval_X")
        if len(ctx.eval_X) != len(ctx.eval_y):
            raise RuntimeError("[ICEvaluateStep] eval_X / eval_y length mismatch")
        if not ctx.eval_X.index.equals(ctx.eval_y.index):
            raise RuntimeError("[ICEvaluateStep] eval_X / eval_y index mismatch")

        predictions = ctx.model_state.model.predict(ctx.eval_X.values)
        ic, _ = spearmanr(predictions, ctx.eval_y.values)

        ctx.metrics[f"ic@{ctx.eval_date}"] = float(ic)
