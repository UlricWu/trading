# filepath: src/training/steps/preprocess.py
"""Fit preprocessing on training rows and transform those rows once."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src import logs
from src.config.model_config import PreprocessingConfig
from src.training.context import TrainingContext
from src.training.engines.preprocessing import FittedPreprocessor


class PreprocessStep:
    """Fit and apply one training-owned preprocessing object.

    Example:
        preprocess = PreprocessStep(model_config.preprocessing)
        train_X, train_y, fitted = preprocess(
            train_X=train_X,
            train_y=train_y,
        )
    """

    def __init__(self, preprocessing_cfg: PreprocessingConfig) -> None:
        """Bind one preprocessing configuration.

        Example:
            preprocess = PreprocessStep(model_config.preprocessing)
        """
        self._preprocessing_cfg = preprocessing_cfg

    def __call__(
        self,
        *,
        train_X: pd.DataFrame,
        train_y: pd.Series,
    ) -> tuple[pd.DataFrame, pd.Series, FittedPreprocessor]:
        """Return aligned retained training rows and their fitted preprocessor.

        Example:
            train_X, train_y, fitted = preprocess(
                train_X=train_X,
                train_y=train_y,
            )
        """
        if len(train_X) != len(train_y) or not train_X.index.equals(train_y.index):
            raise RuntimeError("PreprocessStep train_X / train_y index mismatch")

        fitted = FittedPreprocessor.fit(
            train_X=train_X,
            config=self._preprocessing_cfg,
        )
        keep_rows, values = fitted.transform(
            train_X.to_numpy(dtype=float, na_value=np.nan, copy=True)
        )
        if values.shape[0] == 0:
            raise ValueError("PreprocessStep retained no training rows")

        processed_X = pd.DataFrame(
            values,
            index=train_X.index[keep_rows],
            columns=fitted.feature_names,
        )
        processed_y = train_y.iloc[np.flatnonzero(keep_rows)].copy()
        logs.info(
            f"train_rows={len(train_X)} retained_rows={len(processed_X)} "
            f"skipped_rows={len(train_X) - len(processed_X)}"
        )
        return processed_X, processed_y, fitted

    def run(self, context: TrainingContext) -> TrainingContext:
        """Preprocess the Context training partition and attach fitted state.

        Example:
            next_context = preprocess.run(loaded_context)
        """
        if context.train_X is None or context.train_y is None:
            raise RuntimeError("PreprocessStep requires loaded training data")
        context.train_X, context.train_y, context.preprocess = self(
            train_X=context.train_X,
            train_y=context.train_y,
        )
        return context
