# filepath: tests/training/steps/test_report.py
import json
from datetime import date, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import SGDRegressor

from src import logs
from src.access import meta
from src.training.artifact import save
from src.training.context import EvaluationDay, TrainingContext, TrainingWindow
from src.training.engines.preprocessing import FittedPreprocessor
from src.training.evaluation import evaluate_prediction_record, save_evaluation
from src.training.inference_model import InferenceModel
from src.training.prediction import save_prediction_record
from src.training.steps.report import ReportStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


def _context(pm: PathManager) -> TrainingContext:
    train_X = pd.DataFrame({"f": [0.0, 1.0, 2.0]})
    train_y = pd.Series([0.1, 0.5, 0.9])
    raw_model = SGDRegressor(random_state=7, max_iter=37, shuffle=False)
    raw_model.partial_fit(train_X.to_numpy(), train_y.to_numpy())
    model = InferenceModel(
        model=raw_model,
        preprocess=FittedPreprocessor(feature_names=("f",), missing_method="drop"),
        feature_set="l2_stock_1430", feature_version="v1", train_dates=("2026-07-01",),
        label_set="l2_stock_1430_t1_vwap_rank", label_version="v1", label_column="y_rank_return",
    )
    model_ref = pm.experiment_training_model(experiment_name="run")
    save(model_ref, model)
    day = "2026-07-03"
    decision = DateTimeUtils.local_time_to_utc_epoch_us(time(14, 30), date.fromisoformat(day))
    keys = {"symbol": ["000001", "000002", "000003", "000004"], "trade_date": [day] * 4,
            "decision_ts_utc": [decision] * 4}
    label_ref = pm.label_object(label_set=model.label_set, version=model.label_version, trade_date=day).payload_path
    label_ref.parent.mkdir(parents=True)
    pd.DataFrame({**keys, model.label_column: [0.1, 0.9, np.nan, 0.8]}).to_parquet(label_ref, index=False)
    meta.commit(pm=pm, payload_path=label_ref)
    prediction_ref = model_ref.parent / "predictions" / f"{day}.parquet"
    save_prediction_record(
        prediction_ref, model_ref=model_ref, trade_date=day, decision_ts_us=decision,
        features=pd.DataFrame({**keys, "f": [0.0, np.nan, 1.0, 2.0]}), feature_names=model.feature_names,
        predictions=np.array([0.2, np.nan, 0.7, 0.3]),
    )
    observation = evaluate_prediction_record(prediction_ref, pm=pm)
    save_evaluation(pm.experiment_training_metrics(experiment_name="run"), (observation,))
    return TrainingContext(
        window=TrainingWindow(train_dates=model.train_dates, model_ref=model_ref,
                              evaluations=(EvaluationDay(trade_date=day, decision_ts_us=decision,
                                                         prediction_ref=prediction_ref),)),
        train_X=train_X, train_y=train_y, model=model,
    )


def test_report_writes_diagnostics_and_paths_to_logs_without_retraining_or_repredicting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm)
    expected_train_predictions = context.model.model.predict(context.train_X.to_numpy())
    expected_train_rmse = np.sqrt(np.mean((expected_train_predictions - context.train_y.to_numpy()) ** 2))
    original_model = context.window.model_ref.read_bytes()
    original_predictions = context.window.evaluations[0].prediction_ref.read_bytes()
    metrics_ref = pm.experiment_training_metrics(experiment_name="run")
    original_metrics = metrics_ref.read_bytes()

    def reject_transform(self: FittedPreprocessor, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        raise AssertionError("report must not repeat preprocessing or evaluation inference")

    def reject_fit(self: SGDRegressor, *args: object, **kwargs: object) -> SGDRegressor:
        raise AssertionError("report must not train")

    monkeypatch.setattr(FittedPreprocessor, "transform", reject_transform)
    monkeypatch.setattr(SGDRegressor, "partial_fit", reject_fit)
    context.model = None  # Diagnostics must use the delivered model, not this optional in-memory object.
    job_log = tmp_path / "job.log"
    sink = logs.add(job_log, format="{name} - {message}", filter="src.training.steps.report", level="INFO")
    try:
        assert ReportStep(pm=pm, experiment_name="run").run(context) is context
    finally:
        logs.remove(sink)
    text = job_log.read_text()
    for expected in (
        "src.training.steps.report", "✅ 训练报告; experiment=run\n", "训练与评价覆盖", "排序表现（Rank IC）",
        "误差与基线", "2026-07-01 ~ 2026-07-01（1 个交易日）", "2026-07-03 ~ 2026-07-03（1 个交易日）",
        f"模型：{context.window.model_ref}", f"IC 指标 JSON：{metrics_ref}",
        f"完整诊断 HTML 报告：{pm.experiment_training_report(experiment_name='run')}",
        f"运行配置：{pm.experiment_training_schedule(experiment_name='run')}",
        "平均每日 Rank IC 为正（1.000000）", "正 IC 1 / 1 个有效交易日（100.00%）",
        f"模型 − 基线 = {np.sqrt(0.13) - np.sqrt(0.125):+.6f}（高 1.98%）",
        "逐轮损失未记录，收敛尚未评估", "相对各自样本集的标签均值基线",
    ):
        assert expected in text
    rows = [tuple(cell.strip() for cell in line.strip("|").split("|"))
            for line in text.splitlines() if line.startswith("|")]
    for expected in (
        ("实际训练样本 / 特征数", "3 行 / 1 列"), ("实际训练轮数", "1"),
        ("IC 有效日期 / 评价日期", "1 / 1 个交易日（100.00%）"),
        ("有效配对 / 评价输入", "2 / 4 行（50.00%）"), ("评价排除明细", "无预测 1 行；有预测但缺标签 1 行"),
        ("IC 均值", "1.000000"), ("IC 标准差（ddof=1）", "无值"),
        ("正 IC 天数 / IC 有效日期", "1 / 1（100.00%）"), ("IC 无值原因", "无"),
        ("RMSE（均方根误差）", f"{expected_train_rmse:.6f}", f"{np.sqrt(0.13):.6f}", f"{np.sqrt(0.13) - expected_train_rmse:+.6f}"),
    ):
        assert expected in rows
    errors = {row[0]: row[1:] for row in rows if len(row) == 4}
    assert errors["MAE（平均绝对误差）"][1] == "0.300000"
    assert errors["MAE（平均绝对误差）"][2] == f"{0.3 - np.mean(np.abs(expected_train_predictions - context.train_y.to_numpy())):+.6f}"
    assert errors["MSE（均方误差）"][1] == "0.130000"
    assert errors["R²（决定系数）"][1] == f"{1 - 0.26 / 0.245:.6f}"
    assert errors["固定预测 0.5 基线 RMSE"][1] == f"{np.sqrt(0.125):.6f}"
    assert text.count("src.training.steps.report") == 2  # One start event and one complete multiline report.
    assert text.index("排序评价：") < text.index("训练与评价覆盖")
    assert "eval_rmse=" not in text and "None" not in text
    report = pm.experiment_training_report(experiment_name="run").read_text()
    for expected in ("2026-07-03 14:30:00+08:00", f"{expected_train_rmse:.6f}", f"{np.sqrt(0.13):.6f}", "实际训练轮数：1"):
        assert expected in report
    assert context.window.model_ref.read_bytes() == original_model
    assert context.window.evaluations[0].prediction_ref.read_bytes() == original_predictions
    assert metrics_ref.read_bytes() == original_metrics


@pytest.mark.parametrize("mutation", ["label_revision", "model_reference", "decision_time", "prediction_reference"])
def test_report_rejects_mixed_evaluation_evidence(tmp_path: Path, mutation: str) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm)
    metrics_ref = pm.experiment_training_metrics(experiment_name="run")
    payload = json.loads(metrics_ref.read_text())
    if mutation == "label_revision":
        label_ref = Path(payload["observations"][0]["label_ref"])
        frame = pd.read_parquet(label_ref)
        frame.loc[0, "y_rank_return"] = 0.6
        frame.to_parquet(label_ref, index=False)
        meta.commit(pm=pm, payload_path=label_ref)
    elif mutation == "model_reference":
        payload["observations"][0]["model_ref"] = str(tmp_path / "other.joblib")
    elif mutation == "decision_time":
        payload["observations"][0]["decision_ts_us"] += 1
    else:
        payload["observations"][0]["prediction_ref"] = None
    metrics_ref.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="training report") as raised:
        ReportStep(pm=pm, experiment_name="run").run(context)
    assert "trade_date=2026-07-03" in raised.value.__notes__[0]
    assert f"model_ref={context.window.model_ref}" in raised.value.__notes__[0]
    assert not pm.experiment_training_report(experiment_name="run").exists()


def test_report_requires_the_actual_retained_training_samples(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    context = _context(pm)
    context.train_X = None
    with pytest.raises(RuntimeError, match="retained preprocessed training rows"):
        ReportStep(pm=pm, experiment_name="run").run(context)
