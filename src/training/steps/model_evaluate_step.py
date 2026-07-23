# filepath: src/training/steps/model_evaluate_step.py
from __future__ import annotations

from scipy.stats import spearmanr

from src.pipeline.step import PipelineStep
from src.training.context import TrainingContext


class ICEvaluateStep(PipelineStep[TrainingContext]):
    """
    Evaluate the current model on one in-memory eval date.

    This is the only daily Rank IC evaluation boundary in offline training. It
    writes predictions to `ctx.eval_pred` and the scalar metric to
    `ctx.metrics[f"ic@{ctx.eval_date}"]`; it does not resolve paths, persist
    artifacts, generate reports, or delegate to a separate Rank IC step.
    """

    def run(self, ctx: TrainingContext) -> TrainingContext:
        if ctx.model_state is None:
            raise RuntimeError("[ICEvaluateStep] model_state is missing")
        if ctx.eval_X is None or ctx.eval_y is None:
            raise RuntimeError("[ICEvaluateStep] eval_X / eval_y not set")
        if ctx.eval_X.empty:
            raise RuntimeError("[ICEvaluateStep] eval_X is empty")
        if len(ctx.eval_X) != len(ctx.eval_y):
            raise RuntimeError("[ICEvaluateStep] eval_X / eval_y length mismatch")
        if not ctx.eval_X.index.equals(ctx.eval_y.index):
            raise RuntimeError("[ICEvaluateStep] eval_X / eval_y index mismatch")

        preds = ctx.model_state.model.predict(ctx.eval_X.values)
        ic, _ = spearmanr(preds, ctx.eval_y.values)

        ctx.eval_pred = preds
        ctx.metrics[f"ic@{ctx.eval_date}"] = float(ic)

        return ctx
