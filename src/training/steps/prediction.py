# filepath: src/training/steps/prediction.py
"""Freeze complete prediction rows before loading evaluation labels."""

from __future__ import annotations

import numpy as np

from src import logs
from src.access.feature_label import read_features
from src.training.artifact import load
from src.training.context import TrainingContext
from src.training.prediction import save_prediction_record
from src.utils.path import PathManager


class PredictionStep:
    """Predict from the published model and preserve every original sample key.

    Example:
        step = PredictionStep(pm=path_manager)
        context = step.run(persisted_context)
    """

    def __init__(self, *, pm: PathManager) -> None:
        """Bind feature storage; model identity comes from each window's ref.

        Example:
            step = PredictionStep(pm=path_manager)
        """
        self._pm = pm

    def run(self, context: TrainingContext) -> TrainingContext:
        """Publish predictions.parquet with nullable float64 predictions.

        Example:
            context = step.run(persisted_context)
        """
        window = context.window
        if window.eval_date is None:
            return context
        if window.decision_ts_us is None or window.prediction_ref is None:
            raise RuntimeError("PredictionStep requires a decision time and output ref")
        model = load(window.model_ref)
        features = read_features(
            pm=self._pm, feature_set=model.feature_set, version=model.feature_version,
            trade_date=window.eval_date, feature_names=model.feature_names,
        )
        keys = [column for column in features if column not in model.feature_names]
        if "decision_ts_utc" in keys and not features["decision_ts_utc"].eq(window.decision_ts_us).all():
            raise ValueError("feature decision time does not match the requested prediction time")
        predictions = model.predict(
            features.loc[:, list(model.feature_names)].to_numpy(dtype=np.float64, copy=False)
        )
        output = window.prediction_ref
        save_prediction_record(
            output, model_ref=window.model_ref, trade_date=window.eval_date,
            decision_ts_us=window.decision_ts_us, features=features,
            feature_names=model.feature_names, predictions=predictions,
        )
        context.model = model
        logs.info(f"✅ prediction publish; model_ref={window.model_ref} path={output} rows={len(features)}")
        return context
