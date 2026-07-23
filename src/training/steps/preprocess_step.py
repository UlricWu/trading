# filepath: src/training/steps/preprocess_step.py
from __future__ import annotations

from src import logs
from src.config.model_config import PreprocessingConfig
from src.pipeline.step import PipelineStep
from src.training.context import TrainingContext
from src.training.engines.preprocess_engine import PreprocessEngine


class PreprocessStep(PipelineStep[TrainingContext]):
    """
    Fit preprocessing on the current train partition and transform eval data.

    This step is in-memory only. It does not resolve paths or persist artifacts;
    persistence is handled by the training artifact step.
    """

    def __init__(self, preprocessing_cfg: PreprocessingConfig) -> None:
        super().__init__()
        self.preprocessing_cfg = preprocessing_cfg
        self.engine = PreprocessEngine()

    def run(self, ctx: TrainingContext) -> TrainingContext:
        if ctx.train_X is None or ctx.train_y is None:
            raise RuntimeError("[Preprocess] train_X / train_y not set")

        if ctx.train_X.empty:
            raise RuntimeError("[Preprocess] train_X is empty")

        train_X_proc, artifact = self.engine.fit_transform(
            train_X=ctx.train_X,
            cfg=self.preprocessing_cfg,
        )
        ctx.train_X = train_X_proc
        ctx.train_y = ctx.train_y.loc[train_X_proc.index]
        ctx.preprocess_artifact = artifact

        if ctx.eval_X is not None and ctx.eval_y is not None:
            eval_X_proc = self.engine.transform_with_artifact(
                X=ctx.eval_X,
                artifact=artifact,
            )
            ctx.eval_X = eval_X_proc
            ctx.eval_y = ctx.eval_y.loc[eval_X_proc.index]

        logs.info(
            f"[Preprocess] train_rows={len(ctx.train_X)} "
            f"eval_rows={0 if ctx.eval_X is None else len(ctx.eval_X)}"
        )

        return ctx
