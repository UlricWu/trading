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
        raw_values = train_X.to_numpy(dtype=float, na_value=np.nan, copy=False)
        missing = np.isnan(raw_values)
        keep_rows, values = fitted.transform(raw_values)
        if values.shape[0] == 0:
            raise ValueError("PreprocessStep retained no training rows")

        processed_X = pd.DataFrame(
            values,
            index=train_X.index[keep_rows],
            columns=fitted.feature_names,
        )
        processed_y = train_y.iloc[np.flatnonzero(keep_rows)].copy()
        filled_cells = 0 if fitted.missing_method == "drop" else int(missing.sum())
        logs.info(
            f"✅ 特征预处理; missing_method={fitted.missing_method}\n"
            f"含缺失特征：{int(missing.any(axis=1).sum()):,} 行；填充值：{filled_cells:,} 个单元格；"
            f"移除：{len(train_X) - len(processed_X):,} 行；实际训练样本：{len(processed_X):,} 行"
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
        if context.sample_weight is not None:
            dropped = len(context.sample_weight) != len(context.train_y)
            context.sample_weight = context.sample_weight.loc[context.train_y.index]
            if dropped:
                context.sample_weight /= context.sample_weight.mean()
        return context
