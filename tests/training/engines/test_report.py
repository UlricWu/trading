# filepath: tests/training/engines/test_report.py
from pathlib import Path

import numpy as np
from sklearn.linear_model import SGDRegressor

from src.training.engines.preprocessing import FittedPreprocessor
from src.training.engines.report import build_training_report
from src.training.evaluation import ICEvaluation, evaluate_predictions, summarize_observations
from src.training.inference_model import InferenceModel


def test_report_preserves_all_models_dates_and_undefined_reasons() -> None:
    ref = Path("/data/models/run<1>.joblib")
    model = InferenceModel(
        model=SGDRegressor(random_state=7).fit(np.array([[0.0], [1.0]]), np.array([0.0, 1.0])),
        preprocess=FittedPreprocessor(feature_names=("factor",), missing_method="constant", fill_values=(0.0,)),
        feature_set="daily", feature_version="v1", train_dates=("2026-07-01", "2026-07-02"),
        label_set="rank", label_version="v1", label_column="target",
    )
    observation = evaluate_predictions(model_ref=ref, decision_ts_us=123, predictions=np.array([1.0]), labels=np.array([2.0]))
    report = build_training_report(
        experiment_name="training<&>", models={ref: model, Path("/data/models/2.joblib"): model},
        evaluation=ICEvaluation(observations=(observation,), summary=summarize_observations((observation,))),
    )
    for expected in ("training&lt;&amp;&gt;", "run&lt;1&gt;.joblib", "2.joblib", "2026-07-01", "2026-07-02", "target", "无值", "有效配对不足", "预测为常量", "标签为常量", "0 / 1", "123"):
        assert expected in report
    assert "NaN" not in report and "nan" not in report
