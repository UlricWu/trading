# filepath: src/training/steps/prediction.py
"""Freeze complete prediction rows before loading evaluation labels."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from src import logs
from src.access.feature_label import read_features
from src.training import PROGRESS_INTERVAL_SECONDS
from src.training.artifact import load
from src.training.context import TrainingContext
from src.training.prediction import save_prediction_record
from src.utils.path import PathManager


class PredictionStep:
    """Predict from the published model and preserve every original sample key.

    Example:
        step = PredictionStep(pm=path_manager, clock=monotonic)
        context = step.run(persisted_context)
    """

    def __init__(self, *, pm: PathManager, clock: Callable[[], float]) -> None:
        """Bind feature storage; model identity comes from each window's ref.

        Example:
            step = PredictionStep(pm=path_manager, clock=monotonic)
        """
        self._pm = pm
        self._clock = clock

    def run(self, context: TrainingContext) -> TrainingContext:
        """Publish every evaluation day using the same restored model.

        Example:
            context = step.run(persisted_context)
        """
        window = context.window
        if not window.evaluations:
            return context
        days = len(window.evaluations)
        logs.info(
            f"▶️ 批量预测; days={days}\n"
            f"评价日期：{window.evaluations[0].trade_date} ~ {window.evaluations[-1].trade_date}（{days:,} 个交易日）；复用已保存模型"
        )
        model = load(window.model_ref)
        row_count = 0
        last_progress = self._clock()
        for completed, day in enumerate(window.evaluations, start=1):
            try:
                features = read_features(
                    pm=self._pm, feature_set=model.feature_set, version=model.feature_version,
                    trade_date=day.trade_date, feature_names=model.feature_names,
                )
                if "decision_ts_utc" in features and not features["decision_ts_utc"].eq(day.decision_ts_us).all():
                    raise ValueError("feature decision time does not match the evaluation day")
                predictions = model.predict(
                    features.loc[:, list(model.feature_names)].to_numpy(dtype=np.float64, copy=False)
                )
                save_prediction_record(
                    day.prediction_ref, model_ref=window.model_ref, trade_date=day.trade_date,
                    decision_ts_us=day.decision_ts_us, features=features,
                    feature_names=model.feature_names, predictions=predictions,
                )
            except Exception as exc:
                exc.add_note(f"预测写入; trade_date={day.trade_date} prediction_ref={day.prediction_ref} model_ref={window.model_ref}")
                raise
            row_count += len(features)
            now = self._clock()
            if completed < days and now - last_progress >= PROGRESS_INTERVAL_SECONDS:
                logs.info(f"⏳ 批量预测; trade_date={day.trade_date}\n已写入 {completed:,} / {days:,} 个交易日；累计 {row_count:,} 行")
                last_progress = now
        logs.info(f"✅ 批量预测; days={days}\n已写入 {days:,} 个交易日的预测记录，共 {row_count:,} 行（保留无预测行）")
        context.model = model
        return context
