# filepath: src/training/steps/ic_evaluate.py
"""Read corresponding labels only after the prediction record is fixed."""

from __future__ import annotations

from collections.abc import Callable

from src import logs
from src.training import PROGRESS_INTERVAL_SECONDS
from src.training.context import TrainingContext
from src.training.evaluation import evaluate_prediction_record
from src.utils.path import PathManager


class ICEvaluateStep:
    """Evaluate the saved predictions from every selected evaluation day.

    Example:
        step = ICEvaluateStep(pm=path_manager, clock=monotonic)
        context = step.run(predicted_context)
    """

    def __init__(self, *, pm: PathManager, clock: Callable[[], float]) -> None:
        """Bind label storage without reading it during composition.

        Example:
            step = ICEvaluateStep(pm=path_manager, clock=monotonic)
        """
        self._pm = pm
        self._clock = clock

    def run(self, context: TrainingContext) -> TrainingContext:
        """Evaluate exact key-aligned labels using the model's actual target.

        Example:
            context = step.run(predicted_context)
        """
        days = len(context.window.evaluations)
        if not days:
            return context
        logs.info(f"▶️ IC 评价; days={days}\n按完整样本键配对已保存预测与标签")
        defined_days = 0
        pair_count = 0
        last_progress = self._clock()
        for completed, day in enumerate(context.window.evaluations, start=1):
            try:
                observation = evaluate_prediction_record(day.prediction_ref, pm=self._pm)
            except Exception as exc:
                exc.add_note(f"IC 评价; trade_date={day.trade_date} prediction_ref={day.prediction_ref}")
                raise
            context.observations.append(observation)
            defined_days += observation.value is not None
            pair_count += observation.pair_count
            now = self._clock()
            if completed < days and now - last_progress >= PROGRESS_INTERVAL_SECONDS:
                logs.info(f"⏳ IC 评价; trade_date={day.trade_date}\n已评价 {completed:,} / {days:,} 个交易日")
                last_progress = now
        logs.info(f"✅ IC 评价; days={days}\nIC 有效日期：{defined_days:,} / {days:,} 个交易日；有效配对：{pair_count:,} 行")
        return context
