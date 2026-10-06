# filepath: tests/training/engines/test_report.py
from pathlib import Path

import numpy as np
import pytest
from sklearn.linear_model import SGDRegressor

from src.config.model_config import FeatureLabelConfig, MissingConfig, ModelConfig, PreprocessingConfig
from src.jobs.requests import create_training_submission
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.engines.report import build_training_log, build_training_report, build_training_start_log, summarize_training
from src.training.evaluation import ICEvaluation, evaluate_predictions, summarize_observations
from src.training.inference_model import InferenceModel


def test_report_preserves_model_dates_undefined_reasons_and_diagnostics() -> None:
    ref = Path("/data/models/run<1>.joblib")
    model = InferenceModel(
        model=SGDRegressor(random_state=7).fit(np.array([[0.0], [1.0]]), np.array([0.0, 1.0])),
        preprocess=FittedPreprocessor(feature_names=("factor",), missing_method="constant", fill_values=(0.0,)),
        feature_set="daily", feature_version="v1", train_dates=("2026-07-01", "2026-07-02"),
        label_set="rank", label_version="v1", label_column="target",
    )
    observation = evaluate_predictions(model_ref=ref, decision_ts_us=123, predictions=np.array([1.0]), labels=np.array([2.0]))
    evaluation = ICEvaluation(observations=(observation,), summary=summarize_observations((observation,)))
    summary = summarize_training(
        train_predictions=np.array([0.1, 0.9]), train_labels=np.array([0.0, 1.0]),
        evaluation_predictions=np.array([1.0]), evaluation_labels=np.array([2.0]), evaluation=evaluation,
    )
    report = build_training_report(
        experiment_name="training<&>", model_ref=ref, model=model,
        evaluation=evaluation, summary=summary, actual_iterations=1,
    )
    for expected in (
        "training&lt;&amp;&gt;", "run&lt;1&gt;.joblib", "2026-07-01", "2026-07-02", "target", "无值",
        "有效配对不足", "预测为常量", "标签为常量", "0 / 1", "1970-01-01 08:00:00.000123+08:00",
        "实际训练样本：2", "实际训练轮数：1", "固定预测 0.5 基线 RMSE", "评估 − 训练 RMSE：0.900000",
        "IC 标准差（ddof=1）：无值", "未记录逐轮损失", "无法据此判断收敛", "Asia/Shanghai",
    ):
        assert expected in report
    assert "NaN" not in report and "nan" not in report
    log = build_training_log(
        model=model, evaluation=evaluation, summary=summary, actual_iterations=1,
        model_ref=ref, metrics_ref=Path("/data/metrics.json"), report_ref=Path("/data/report.html"),
        schedule_ref=Path("/data/schedule.json"),
    )
    rows = [tuple(cell.strip() for cell in line.strip("|").split("|"))
            for line in log.splitlines() if line.startswith("|")]
    assert ("指标", "训练集", "评估集", "评估 − 训练") in rows
    assert ("R²（决定系数）", "0.960000", "无值", "—") in rows
    for expected in ("有效配对不足 1 个交易日", "预测为常量 1 个交易日", "标签为常量 1 个交易日", "-0.500000", "+0.900000"):
        assert expected in log
    assert "None" not in log and "nan" not in log


def test_summary_pools_regression_pairs_but_weights_defined_ic_dates_equally() -> None:
    predictions = (np.array([0.0, 0.5, np.nan, 1.0]), np.array([0.8, 0.2]), np.array([0.0]))
    labels = (np.array([0.0, 1.0, 1.0, np.nan]), np.array([0.0, 1.0]), np.array([1.0]))
    observations = tuple(evaluate_predictions(
        model_ref=Path("/data/model.joblib"), decision_ts_us=index, predictions=x, labels=y,
    ) for index, (x, y) in enumerate(zip(predictions, labels, strict=True)))
    evaluation = ICEvaluation(observations=observations, summary=summarize_observations(observations))
    summary = summarize_training(
        train_predictions=np.array([0.1, 0.9]), train_labels=np.array([0.0, 1.0]),
        evaluation_predictions=np.concatenate(predictions), evaluation_labels=np.concatenate(labels), evaluation=evaluation,
    )
    assert (summary.input_count, summary.no_prediction_count, summary.missing_label_count, summary.evaluation.sample_count) == (7, 1, 1, 5)
    assert evaluation.summary.mean_ic == 0.0
    assert summary.ic_std == pytest.approx(np.sqrt(2))
    assert (summary.positive_ic_days, summary.positive_ic_ratio) == (1, 0.5)
    assert summary.train.sample_count == 2
    assert summary.train.mae == pytest.approx(0.1)
    assert summary.train.mse == pytest.approx(0.01)
    assert summary.train.rmse == pytest.approx(0.1)
    assert summary.train.r2 == pytest.approx(0.96)
    assert summary.evaluation.mae == pytest.approx(0.62)
    assert summary.evaluation.mse == pytest.approx(2.53 / 5)
    assert summary.evaluation.rmse == pytest.approx(np.sqrt(2.53 / 5))
    assert summary.evaluation.r2 == pytest.approx(1 - 2.53 / 1.2)
    assert summary.evaluation.baseline_rmse == 0.5
    assert summary.mae_gap == pytest.approx(0.52)
    assert summary.rmse_gap == pytest.approx(np.sqrt(2.53 / 5) - 0.1)
    assert summary.rmse_vs_baseline == pytest.approx(np.sqrt(2.53 / 5) - 0.5)


@pytest.mark.parametrize("predictions,labels,expected_rmse", [
    ([np.nan], [1.0], None), ([1.0], [np.nan], None), ([], [], None),
    ([0.0], [0.5], 0.5), ([0.0, 1.0], [0.5, 0.5], 0.5),
])
def test_summary_retains_undefined_metrics_and_zero_baseline(
    predictions: list[float], labels: list[float], expected_rmse: float | None,
) -> None:
    x, y = np.array(predictions), np.array(labels)
    observation = evaluate_predictions(model_ref=Path("/data/model.joblib"), decision_ts_us=1, predictions=x, labels=y)
    evaluation = ICEvaluation(observations=(observation,), summary=summarize_observations((observation,)))
    summary = summarize_training(
        train_predictions=np.array([0.0, 1.0]), train_labels=np.array([0.0, 1.0]),
        evaluation_predictions=x, evaluation_labels=y, evaluation=evaluation,
    )
    assert summary.ic_std is None and summary.positive_ic_ratio is None
    assert summary.positive_ic_days == 0 and summary.evaluation.r2 is None
    assert summary.evaluation.rmse == expected_rmse
    if expected_rmse is None:
        assert summary.evaluation.mae is None and summary.evaluation.mse is None
        assert summary.mae_gap is None and summary.rmse_gap is None and summary.rmse_vs_baseline is None
    else:
        assert summary.evaluation.baseline_rmse == 0.0
        assert summary.rmse_vs_baseline == expected_rmse


@pytest.mark.parametrize("predictions,labels", [
    (np.array([[0.0]]), np.array([0.0])), (np.array([]), np.array([0.0])),
    (np.array([np.inf]), np.array([np.nan])), (np.array([np.nan]), np.array([-np.inf])),
])
def test_summary_rejects_malformed_or_infinite_regression_inputs(predictions: np.ndarray, labels: np.ndarray) -> None:
    evaluation = ICEvaluation(observations=(), summary=summarize_observations(()))
    with pytest.raises(ValueError):
        summarize_training(
            train_predictions=np.array([0.0]), train_labels=np.array([0.0]),
            evaluation_predictions=predictions, evaluation_labels=labels, evaluation=evaluation,
        )


@pytest.mark.parametrize("predictions,labels,expected_ranking,expected_regression,expected_coverage", [
    ([0.0, 1.0], [0.0, 1.0], "为正（1.000000）", "= -0.500000（低 100.00%）", "2 / 2 行（100.00%）"),
    ([1.0, 0.0], [0.0, 1.0], "为负（-1.000000）", "= +0.500000（高 100.00%）", "2 / 2 行（100.00%）"),
    ([0.25, 0.5, 0.75, 1.0], [0.5, 1.0, 0.25, 0.75], "为零（0.000000）", "= +0.089098（高 29.10%）", "4 / 4 行（100.00%）"),
    ([0.5, 0.5], [0.0, 1.0], "无值（没有 IC 有定义的评价日）", "= +0.000000（相同，0.00%）", "2 / 2 行（100.00%）"),
    ([0.0, 1.0], [0.5, 0.5], "无值（没有 IC 有定义的评价日）", "= +0.500000（相对变化无值：基线 RMSE 为 0）", "2 / 2 行（100.00%）"),
    ([0.5, 0.5], [0.5, 0.5], "无值（没有 IC 有定义的评价日）", "= +0.000000（相对变化无值：基线 RMSE 为 0）", "2 / 2 行（100.00%）"),
    ([np.nan, 1.0], [0.0, np.nan], "无值（没有 IC 有定义的评价日）", "回归评价：无值（没有有效预测与标签配对）", "0 / 2 行（0.00%）"),
    ([], [], "无值（没有 IC 有定义的评价日）", "回归评价：无值（没有有效预测与标签配对）", "0 / 0 行（无值：无输入样本）"),
])
def test_log_and_html_explain_comparisons_without_inventing_quality_or_coverage(
    predictions: list[float], labels: list[float], expected_ranking: str,
    expected_regression: str, expected_coverage: str,
) -> None:
    ref = Path("/data/model.joblib")
    model = InferenceModel(
        model=SGDRegressor(random_state=7),
        preprocess=FittedPreprocessor(feature_names=("f",), missing_method="drop"),
        feature_set="daily", feature_version="v1", train_dates=("2026-07-01",),
        label_set="rank", label_version="v1", label_column="target",
    )
    x, y = np.array(predictions), np.array(labels)
    observation = evaluate_predictions(model_ref=ref, decision_ts_us=1, predictions=x, labels=y)
    evaluation = ICEvaluation(observations=(observation,), summary=summarize_observations((observation,)))
    summary = summarize_training(
        train_predictions=np.array([0.0, 1.0]), train_labels=np.array([0.0, 1.0]),
        evaluation_predictions=x, evaluation_labels=y, evaluation=evaluation,
    )
    text = build_training_log(
        model=model, evaluation=evaluation, summary=summary, actual_iterations=1,
        model_ref=ref, metrics_ref=Path("/data/metrics.json"), report_ref=Path("/data/report.html"),
        schedule_ref=Path("/data/schedule.json"),
    )
    html = build_training_report(
        experiment_name="run", model_ref=ref, model=model, evaluation=evaluation,
        summary=summary, actual_iterations=1,
    )
    assert expected_coverage in text
    for document in (text, html):
        assert expected_ranking in document and expected_regression in document
        assert "收敛尚未评估" in document
        assert "不等于预测准确率或交易胜率" in document
        assert "nan" not in document and "inf" not in document and "None" not in document


@pytest.mark.parametrize("method,fill_value,description", [
    ("constant", 0.0, "用常量 0.0 填充"), ("mean", None, "用训练集各列均值填充"),
    ("median", None, "用训练集各列中位数填充"), ("drop", None, "训练移除缺失行；预测保留原行并标记无预测"),
])
def test_start_log_explains_actual_missing_method_without_dumping_full_parameters(
    method: str, fill_value: float | None, description: str,
) -> None:
    submission = create_training_submission(
        model="sgd_regression", train_range={"start": "2026-07-01", "end": "2026-07-31"},
        dataset={"feature_group": "daily_basic_selected_v1", "label_group": "daily_close_return_rank_d1/v1"},
    )
    configuration = ModelConfig(
        group="sgd_regression", model_params={"random_state": 7, "alpha": 0.0005},
        dataset=FeatureLabelConfig(feature_set="daily", feature_version="v1", feature_columns=["actual_column"],
                                   label_set="rank", label_version="v1", label_column="target"),
        preprocessing=PreprocessingConfig(missing=MissingConfig(method=method, fill_value=fill_value)),
    )
    text = build_training_start_log(submission=submission, configuration=configuration, target_description="收益排名")
    assert description in text
    assert "daily_basic_selected_v1（1 列）" in text
    assert "本次未请求评价" in text and "schedule.json" in text
    assert "alpha" not in text and "actual_column" not in text
