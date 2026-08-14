# filepath: src/training/steps/preprocess_step.py
"""Fit train-owned preprocessing and transform one evaluation partition."""

from __future__ import annotations

import pandas as pd

from src import logs
from src.config.model_config import PreprocessingConfig
from src.training.artifact import PreprocessArtifact
from src.training.context import TrainingContext
from src.training.engines.preprocess_engine import PreprocessEngine
from src.utils import table_ops


class PreprocessStep:
    """Apply one preprocessing config with no shared workflow context.

    Example:
        preprocess = PreprocessStep(model_config.preprocessing)
        prepared = preprocess(
            train_X=train_X,
            train_y=train_y,
            eval_X=eval_X,
            eval_y=eval_y,
        )
    """

    def __init__(self, preprocessing_cfg: PreprocessingConfig) -> None:
        """Bind one preprocessing configuration.

        Example:
            preprocess = PreprocessStep(model_config.preprocessing)
        """
        self._preprocessing_cfg = preprocessing_cfg
        self._engine = PreprocessEngine()

    def __call__(
        self,
        *,
        train_X: pd.DataFrame,
        train_y: pd.Series,
        eval_X: pd.DataFrame,
        eval_y: pd.Series,
    ) -> tuple[
        pd.DataFrame,
        pd.Series,
        pd.DataFrame,
        pd.Series,
        PreprocessArtifact,
    ]:
        """Return aligned train/eval partitions and the fitted artifact.

        Example:
            train_X, train_y, eval_X, eval_y, artifact = preprocess(
                train_X=train_X,
                train_y=train_y,
                eval_X=eval_X,
                eval_y=eval_y,
            )
        """
        processed_train_X, artifact = self._engine.fit_transform(
            train_X=train_X,
            cfg=self._preprocessing_cfg,
        )
        table_ops.require_nonempty(
            processed_train_X,
            who="PreprocessStep train_X",
        )
        processed_train_y = train_y.loc[processed_train_X.index]
        processed_eval_X = self._engine.transform_with_artifact(
            X=eval_X,
            artifact=artifact,
        )
        processed_eval_y = eval_y.loc[processed_eval_X.index]
        logs.info(
            f"[Preprocess] train_rows={len(processed_train_X)} "
            f"eval_rows={len(processed_eval_X)}"
        )
        return (
            processed_train_X,
            processed_train_y,
            processed_eval_X,
            processed_eval_y,
            artifact,
        )

    def run(self, context: TrainingContext) -> TrainingContext:
        """Preprocess the loaded Context partitions and attach the artifact.

        Example:
            next_context = preprocess.run(loaded_context)
        """
        if (
            context.train_X is None
            or context.train_y is None
            or context.eval_X is None
            or context.eval_y is None
        ):
            raise RuntimeError("PreprocessStep requires loaded dataset partitions")
        (
            context.train_X,
            context.train_y,
            context.eval_X,
            context.eval_y,
            context.preprocess,
        ) = self(
            train_X=context.train_X,
            train_y=context.train_y,
            eval_X=context.eval_X,
            eval_y=context.eval_y,
        )
        return context
