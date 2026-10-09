# filepath: tests/workflows/test_offline_training.py
"""Range submissions, one model delivery, and independent daily evaluation."""

from collections.abc import Iterator
from datetime import date, time
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
import yaml

from src import logs
from src.access import meta
from src.config.model_config import FeatureGroupConfig, MissingConfig, ModelPreset, PreprocessingConfig
from src.jobs.requests import TrainingSubmission, create_training_submission
from src.training.artifact import load
from src.training.evaluation import evaluate_prediction_record, load_evaluation
from src.training.models.sgd_regression import train_sgd_regression
from src.training.prediction import verify_prediction_record
from src.training.record import TrainingRunRecord
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager
from src.workflows.offline_training import run_offline_training


@pytest.fixture
def training_log(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "job.log"
    sink = logs.add(path, format="{name} - {message}", level="INFO")
    try:
        yield path
    finally:
        logs.remove(sink)


@pytest.fixture
def settings() -> tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]]:
    source = Path(__file__).parents[2] / "src/config/base.yml"
    document = yaml.safe_load(source.read_text())
    return (
        {key: ModelPreset.model_validate(value) for key, value in document["models"].items()},
        {key: FeatureGroupConfig.model_validate(value) for key, value in document["feature_groups"].items()},
    )


def _ts(day: str, hour: int = 15, minute: int = 0) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(time(hour, minute), date.fromisoformat(day))


def _calendar(
    pm: PathManager, years: tuple[int, ...] = (2026,),
    closed: tuple[str, ...] = (), opened: tuple[str, ...] = (),
) -> None:
    for year in years:
        dates = pd.date_range(f"{year}-01-01", f"{year}-12-31")
        days = dates.strftime("%Y-%m-%d")
        is_open = ((dates.dayofweek < 5) & ~days.isin(closed)) | days.isin(opened)
        path = pm.processed_year_object(
            dataset_name="trade_calendar", version="v1", calendar_year=year,
        ).payload_path
        path.parent.mkdir(parents=True)
        pd.DataFrame({"trade_date": days, "is_open": is_open}).to_parquet(path, index=False)
        meta.commit(pm=pm, payload_path=path)


def _partition(
    pm: PathManager, day: str, feature: FeatureGroupConfig,
    label_set: str = "daily_close_return_rank_d1",
    feature_values: tuple[float, ...] = (0.0, 0.2, 0.8),
    label_values: tuple[float, ...] = (0.25, 0.5, 1.0),
) -> None:
    keys = {"symbol": [f"{index:06d}" for index in range(len(feature_values))],
            "trade_date": [day] * len(feature_values)}
    if feature.feature_set == "l2_stock_1430":
        keys["decision_ts_utc"] = [_ts(day, 14, 30)] * len(feature_values)
    if feature.feature_set == "previous_day_daily_l2":
        keys["decision_ts_utc"] = [_ts(day, 9)] * len(feature_values)
    feature_ref = pm.feature_object(
        feature_set=feature.feature_set, version=feature.feature_version, trade_date=day,
    ).payload_path
    label_ref = pm.label_object(label_set=label_set, version="v1", trade_date=day).payload_path
    for ref, values in (
        (feature_ref, {column: feature_values for column in feature.feature_columns}),
        (label_ref, {"y_rank_return": label_values}),
    ):
        if ref == feature_ref and feature.feature_set == "previous_day_daily_l2":
            values.update({column: feature_values for column in ("baseline_amount", "baseline_momentum", "baseline_reversal")})
        ref.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({**keys, **values}).to_parquet(ref, index=False)
        meta.commit(pm=pm, payload_path=ref)


def _submission(
    train_start: str = "2026-07-01", train_end: str = "2026-07-01",
    eval_start: str | None = None, eval_end: str | None = None,
    feature_group: str = "daily_basic_selected_v1",
    label_group: str = "daily_close_return_rank_d1/v1",
) -> TrainingSubmission:
    return create_training_submission(
        train_range={"start": train_start, "end": train_end},
        eval_range={"start": eval_start, "end": eval_end} if eval_start is not None else None,
        model="sgd_regression", dataset={"feature_group": feature_group, "label_group": label_group},
    )


def test_pure_training_records_resolved_choices_without_historical_claims(
    tmp_path: Path, settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
    training_log: Path,
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    _partition(pm, "2026-07-01", groups["daily_basic_all_v1"])
    submission = _submission()
    assert run_offline_training(
        models=models, feature_groups=groups, path_manager=pm,
        submission=submission, experiment_id="pure",
    ) is None
    name = "training_2026-07-01_2026-07-01_pure"
    ref = pm.experiment_training_model(experiment_name=name)
    model = load(ref)
    assert model.train_dates == ("2026-07-01",)
    assert model.label_set == "daily_close_return_rank_d1"
    assert model.feature_names == tuple(groups["daily_basic_selected_v1"].feature_columns)
    record = TrainingRunRecord.model_validate_json(
        pm.experiment_training_schedule(experiment_name=name).read_text()
    )
    assert record.request == submission.model_dump(mode="json")
    assert record.model_ref == ref and record.eval_dates == ()
    assert record.configuration.model_params == models["sgd_regression"].model_params
    assert record.label_maturity_ts_us == _ts("2026-07-02")
    assert not {"training_cutoff_ts_us", "label_availability", "availability_ref"} & record.model_dump().keys()
    assert not (ref.parent / "predictions").exists()
    assert not pm.experiment_training_metrics(experiment_name=name).exists()
    assert not pm.experiment_training_report(experiment_name=name).exists()
    text = training_log.read_text()
    for expected in (
        "▶️ 训练任务;", "sgd_regression", "daily_basic_selected_v1", "daily_close_return_rank_d1/v1",
        "复权收盘收益的横截面百分位排名", "constant", "partial_fit", "实际训练轮数：1",
        "本次未请求评价", "✅ 训练任务;", "步骤耗时合计", "不代表任务总耗时",
        f"模型：{ref}", f"运行配置：{pm.experiment_training_schedule(experiment_name=name)}",
    ):
        assert expected in text
    assert "HTML 报告：" not in text and "IC 指标 JSON：" not in text
    groups["daily_basic_selected_v1"].feature_columns.reverse()
    assert TrainingRunRecord.model_validate_json(
        pm.experiment_training_schedule(experiment_name=name).read_text()
    ).configuration.dataset.feature_columns == list(model.feature_names)


@pytest.mark.parametrize("eval_end", ["2026-07-03", "2026-07-06"])
def test_single_and_multi_day_evaluation_fit_once_and_preserve_all_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, eval_end: str,
    settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
    training_log: Path,
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    for day in ("2026-07-01", "2026-07-03", "2026-07-06"):
        _partition(pm, day, groups["daily_basic_all_v1"])
    trainer = Mock(wraps=train_sgd_regression)
    monkeypatch.setattr("src.workflows.offline_training.get_model_trainer", Mock(return_value=trainer))
    submission = _submission(eval_start="2026-07-03", eval_end=eval_end)
    run_offline_training(models=models, feature_groups=groups, path_manager=pm, submission=submission, experiment_id="eval")
    name = f"training_2026-07-01_{eval_end}_eval"
    ref = pm.experiment_training_model(experiment_name=name)
    assert trainer.call_count == 1
    assert trainer.call_args.kwargs["X"].shape == (3, 7)
    assert list(pm.experiment_training_dir(experiment_name=name).rglob("*.joblib")) == [ref]
    expected_days = ("2026-07-03",) if eval_end == "2026-07-03" else ("2026-07-03", "2026-07-06")
    evaluation = load_evaluation(pm.experiment_training_metrics(experiment_name=name))
    assert evaluation.summary.date_count == evaluation.summary.defined_date_count == len(expected_days)
    assert {item.model_ref for item in evaluation.observations} == {str(ref)}
    model_bytes = ref.read_bytes()
    for day in expected_days:
        prediction = ref.parent / "predictions" / f"{day}.parquet"
        rows = pd.read_parquet(prediction)
        assert len(rows) == 3 and rows["model_ref"].eq(str(ref)).all()
        assert rows["decision_ts_us"].eq(_ts(day)).all()
        verify_prediction_record(prediction, pm=pm)
    assert ref.read_bytes() == model_bytes
    assert str(ref) in pm.experiment_training_report(experiment_name=name).read_text()
    text = training_log.read_text()
    assert text.count("✅ 批量预测;") == 1
    assert text.count("✅ IC 评价;") == 1
    assert text.count("✅ 训练报告;") == 1
    assert "prediction publish" not in text
    assert "排序评价：" in text and "回归评价：" in text
    assert "诊断计算与报告输出" in text
    assert "不等于预测准确率或交易胜率" in text
    assert "相对各自样本集的标签均值基线" in text
    assert text.index("▶️ 训练任务;") < text.index("▶️ 数据准备;")
    assert text.index("✅ 模型写入;") < text.index("▶️ 批量预测;")
    assert text.index("✅ 训练报告;") < text.index("✅ 训练任务;")


@pytest.mark.parametrize(("feature_group", "label_group", "column_count", "hour", "minute"), [
    ("daily_basic_selected_v1", "daily_close_return_rank_d1/v1", 7, 15, 0),
    ("daily_basic_all_v1", "daily_close_return_rank_d3/v1", 16, 15, 0),
    ("daily_basic_all_v1", "daily_close_return_rank_d5/v1", 16, 15, 0),
    ("l2_stock_1430_all_v1", "l2_stock_1430_t1_vwap_rank/v1", 32, 14, 30),
])
def test_each_configured_group_changes_actual_model_inputs_and_target(
    tmp_path: Path, feature_group: str, label_group: str, column_count: int, hour: int, minute: int,
    settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    label_set = label_group.split("/")[0]
    for day in ("2026-07-01", "2026-07-10"):
        _partition(pm, day, groups[feature_group], label_set)
    run_offline_training(
        models=models, feature_groups=groups, path_manager=pm, experiment_id="choice",
        submission=_submission(eval_start="2026-07-10", eval_end="2026-07-10",
                               feature_group=feature_group, label_group=label_group),
    )
    name = "training_2026-07-01_2026-07-10_choice"
    model = load(pm.experiment_training_model(experiment_name=name))
    assert model.feature_names == tuple(groups[feature_group].feature_columns)
    assert model.model.n_features_in_ == column_count
    assert model.label_set == label_set
    prediction = pm.experiment_training_dir(experiment_name=name) / "predictions/2026-07-10.parquet"
    assert pd.read_parquet(prediction)["decision_ts_us"].eq(_ts("2026-07-10", hour, minute)).all()
    if feature_group == "l2_stock_1430_all_v1":
        assert "decision_ts_utc" in pd.read_parquet(prediction)


@pytest.mark.parametrize(("feature_group", "label_group", "eval_start"), [
    ("l2_stock_1430_all_v1", "l2_stock_1430_t1_vwap_rank/v1", "2026-07-02"),
    ("daily_basic_selected_v1", "daily_close_return_rank_d5/v1", "2026-07-06"),
])
def test_label_lookahead_cannot_cross_first_evaluation_prediction(
    tmp_path: Path, feature_group: str, label_group: str, eval_start: str,
    settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    with pytest.raises(ValueError, match="label that matures after"):
        run_offline_training(
            models=models, feature_groups=groups, path_manager=pm, experiment_id="invalid",
            submission=_submission(eval_start=eval_start, eval_end=eval_start,
                                   feature_group=feature_group, label_group=label_group),
        )
    assert list((tmp_path / "experiments").iterdir()) == []


@pytest.mark.parametrize(("train_start", "train_end", "eval_start", "eval_end", "message"), [
    ("2026-07-04", "2026-07-05", None, None, "train_range contains no"),
    ("2026-07-01", "2026-07-01", "2026-07-04", "2026-07-05", "eval_range contains no"),
    ("2026-07-01", "2026-07-03", "2026-07-03", "2026-07-06", "must precede"),
    ("2026-07-06", "2026-07-06", "2026-07-01", "2026-07-03", "must precede"),
])
def test_empty_or_overlapping_intervals_fail_without_publishing(
    tmp_path: Path, train_start: str, train_end: str, eval_start: str | None,
    eval_end: str | None, message: str,
    settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    with pytest.raises(ValueError, match=message):
        run_offline_training(
            models=models, feature_groups=groups, path_manager=pm, experiment_id="invalid",
            submission=_submission(train_start, train_end, eval_start, eval_end),
        )
    assert list((tmp_path / "experiments").iterdir()) == []


def test_cross_year_and_closed_endpoints_use_the_supplied_calendar(
    tmp_path: Path, settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm, years=(2026, 2027), closed=("2026-12-30", "2027-01-01"), opened=("2027-01-02",))
    for day in ("2026-12-31", "2027-01-02"):
        _partition(pm, day, groups["daily_basic_all_v1"])
    run_offline_training(
        models=models, feature_groups=groups, path_manager=pm, experiment_id="calendar",
        submission=_submission("2026-12-30", "2027-01-03"),
    )
    ref = pm.experiment_training_model(experiment_name="training_2026-12-30_2027-01-03_calendar")
    assert load(ref).train_dates == ("2026-12-31", "2027-01-02")


@pytest.mark.parametrize("missing_object", ["feature", "label"])
def test_missing_training_day_is_not_silently_removed(
    tmp_path: Path, settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
    missing_object: str,
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    for day in ("2026-07-01", "2026-07-02"):
        _partition(pm, day, groups["daily_basic_all_v1"])
    if missing_object == "feature":
        missing = pm.feature_object(
            feature_set="tushare_daily_basic", version="v1", trade_date="2026-07-02",
        )
    else:
        missing = pm.label_object(
            label_set="daily_close_return_rank_d1", version="v1", trade_date="2026-07-02",
        )
    missing.payload_path.unlink()
    missing.meta_path.unlink()
    with pytest.raises(FileNotFoundError):
        run_offline_training(
            models=models, feature_groups=groups, path_manager=pm, experiment_id="missing",
            submission=_submission(train_end="2026-07-02"),
        )
    ref = pm.experiment_training_model(experiment_name="training_2026-07-01_2026-07-02_missing")
    assert not ref.exists()


def test_missing_evaluation_day_fails_but_preserves_published_model_and_earlier_predictions(
    tmp_path: Path, settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
    training_log: Path,
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    for day in ("2026-07-01", "2026-07-03"):
        _partition(pm, day, groups["daily_basic_all_v1"])
    with pytest.raises(FileNotFoundError) as raised:
        run_offline_training(
            models=models, feature_groups=groups, path_manager=pm, experiment_id="missing",
            submission=_submission(eval_start="2026-07-03", eval_end="2026-07-06"),
        )
    name = "training_2026-07-01_2026-07-06_missing"
    ref = pm.experiment_training_model(experiment_name=name)
    assert load(ref).train_dates == ("2026-07-01",)
    assert (ref.parent / "predictions/2026-07-03.parquet").exists()
    assert not pm.experiment_training_metrics(experiment_name=name).exists()
    assert any("trade_date=2026-07-06" in note for note in raised.value.__notes__)
    assert any("step=PredictionStep" in note for note in raised.value.__notes__)
    text = training_log.read_text()
    assert f"✅ 模型写入; model_ref={ref}" in text
    assert "❌ 步骤耗时;" in text
    assert "✅ 批量预测;" not in text and "✅ 训练任务;" not in text
    assert "✅ 训练报告;" not in text and "Traceback" not in text


@pytest.mark.parametrize("missing", ["model", "feature", "family"])
def test_missing_configuration_or_unknown_family_fails_before_calendar_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str,
    settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
) -> None:
    models, groups = settings
    if missing == "model":
        models.clear()
    elif missing == "feature":
        groups.clear()
    else:
        models["sgd_regression"].group = "unknown"
    access = Mock(side_effect=AssertionError("must not read calendar"))
    monkeypatch.setattr("src.workflows.offline_training.Access", access)
    with pytest.raises(ValueError, match="not configured|unsupported model group"):
        run_offline_training(
            models=models, feature_groups=groups, path_manager=PathManager(tmp_path),
            submission=_submission(), experiment_id="missing",
        )
    access.assert_not_called()


def test_retraining_is_deterministic_and_evaluation_labels_do_not_change_model_or_predictions(
    tmp_path: Path, settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    models["sgd_regression"].model_params = {"random_state": 7, "alpha": 0.001, "shuffle": False}
    models["sgd_regression"].preprocessing = PreprocessingConfig(missing=MissingConfig(method="drop"))
    group = groups["daily_basic_selected_v1"]
    for day in ("2026-07-01", "2026-07-02", "2026-07-06"):
        _partition(pm, day, group, feature_values=(0.0, np.nan, 2.0, 3.0), label_values=(0.0, 0.5, 1.0, 1.5))
    submission = _submission(train_end="2026-07-02", eval_start="2026-07-06", eval_end="2026-07-06")
    fitted = []
    for run_id in ("first", "second"):
        run_offline_training(models=models, feature_groups=groups, path_manager=pm, submission=submission, experiment_id=run_id)
        ref = pm.experiment_training_model(experiment_name=f"training_2026-07-01_2026-07-06_{run_id}")
        fitted.append(load(ref))
    first, second = fitted
    assert first.preprocess == second.preprocess
    assert first.model is not second.model
    for field in ("coef_", "intercept_"):
        np.testing.assert_array_equal(getattr(first.model, field), getattr(second.model, field), strict=True)
    for field in ("t_", "n_iter_"):
        assert getattr(first.model, field) == getattr(second.model, field)
    inputs = np.repeat(np.array([[0.0], [np.nan], [1.0], [3.0]]), len(group.feature_columns), axis=1)
    np.testing.assert_array_equal(first.predict(inputs), second.predict(inputs), strict=True)
    prediction = ref.parent / "predictions/2026-07-06.parquet"
    before_model, before_prediction = ref.read_bytes(), prediction.read_bytes()
    before_observation = evaluate_prediction_record(prediction, pm=pm)
    label_ref = pm.label_object(label_set="daily_close_return_rank_d1", version="v1", trade_date="2026-07-06").payload_path
    changed = pd.read_parquet(label_ref)
    changed["y_rank_return"] = np.nan
    changed.to_parquet(label_ref, index=False)
    meta.commit(pm=pm, payload_path=label_ref)
    after_observation = evaluate_prediction_record(prediction, pm=pm)
    assert before_observation.pair_count == 3 and after_observation.pair_count == 0
    assert ref.read_bytes() == before_model and prediction.read_bytes() == before_prediction


def test_gap_counts_trading_sessions_and_does_not_shift_explicit_evaluation(
    tmp_path: Path, settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    for day in ("2026-07-03", "2026-07-06", "2026-07-07"):
        _partition(pm, day, groups["daily_basic_selected_v1"])
    request = _submission(train_start="2026-07-03", train_end="2026-07-03", eval_start="2026-07-06", eval_end="2026-07-06")
    request = TrainingSubmission.model_validate({**request.model_dump(), "eval_gap_sessions": 1})
    with pytest.raises(ValueError, match="eval_gap_sessions"):
        run_offline_training(models=models, feature_groups=groups, path_manager=pm, submission=request, experiment_id="bad-gap")
    request = TrainingSubmission.model_validate({**request.model_dump(), "eval_range": {"start": "2026-07-07", "end": "2026-07-07"}})
    run_offline_training(models=models, feature_groups=groups, path_manager=pm, submission=request, experiment_id="good-gap")
    root = pm.experiment_training_dir(experiment_name="training_2026-07-03_2026-07-07_good-gap")
    assert [path.name for path in (root / "predictions").iterdir()] == ["2026-07-07.parquet"]


def test_134_day_development_and_17_day_evaluation_use_one_frozen_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]],
) -> None:
    from src.access import Access
    from src.training.models.catalog import get_model_trainer

    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    sessions = Access(pm=pm, processed_version="v1").trade_dates(start_date="2026-01-01", end_date="2026-12-31")
    development, evaluation = sessions[:134], sessions[135:152]
    for day in (*development, *evaluation):
        _partition(pm, day, groups["previous_day_daily_l2_v1"], "next_day_high_return_rank")
    trainer = Mock(wraps=get_model_trainer("ridge_regression"))
    monkeypatch.setattr("src.workflows.offline_training.get_model_trainer", Mock(return_value=trainer))
    request = create_training_submission(
        train_range={"start": development[0], "end": development[-1]},
        eval_range={"start": evaluation[0], "end": evaluation[-1]},
        validation_sessions=27, eval_gap_sessions=1, model="ridge_regression",
        dataset={"feature_group": "previous_day_daily_l2_v1", "label_group": "next_day_high_return_rank/v1"},
    )
    name = f"training_{development[0]}_{evaluation[-1]}_split"
    run_offline_training(models=models, feature_groups=groups, path_manager=pm, submission=request, experiment_id="split")
    root = pm.experiment_training_dir(experiment_name=name)
    record = TrainingRunRecord.model_validate_json((root / "schedule.json").read_text())
    assert trainer.call_count == 1
    assert record.train_dates == tuple(development[:106])
    assert record.purged_dates == (development[106],)
    assert record.validation_dates == tuple(development[107:])
    assert record.eval_dates == tuple(evaluation)
    assert trainer.call_args.kwargs["X"].shape == (106 * 3, 39)
    assert load_evaluation(root / "metrics.json").summary.date_count == 17
    assert load_evaluation(root / "validation-metrics.json").summary.date_count == 27
    assert len(list(root.rglob("*.joblib"))) == 1
    assert len(list((root / "predictions").iterdir())) == 44
    for path in (root / "predictions").iterdir():
        verify_prediction_record(path, pm=pm)
    summary = pd.read_json(root / "selection-summary.json")
    assert summary.groupby("stage").days.first().to_dict() == {"validation": 27, "evaluation": 17}

    for day in (*record.validation_dates, *record.eval_dates):
        _partition(pm, day, groups["previous_day_daily_l2_v1"], "next_day_high_return_rank", label_values=(np.nan,) * 3)
    run_offline_training(models=models, feature_groups=groups, path_manager=pm, submission=request, experiment_id="changed-labels")
    changed = pm.experiment_training_dir(experiment_name=name.removesuffix("split") + "changed-labels")
    assert (root / "model.joblib").read_bytes() == (changed / "model.joblib").read_bytes()
    for path in (root / "predictions").iterdir():
        np.testing.assert_array_equal(pd.read_parquet(path).prediction, pd.read_parquet(changed / "predictions" / path.name).prediction)
    assert all(row.pair_count == 0 for row in load_evaluation(changed / "metrics.json").observations)


@pytest.mark.parametrize("validation_sessions", [1, 2])
def test_validation_split_rejects_missing_mature_training_dates(
    tmp_path: Path, settings: tuple[dict[str, ModelPreset], dict[str, FeatureGroupConfig]], validation_sessions: int,
) -> None:
    models, groups = settings
    pm = PathManager(tmp_path)
    _calendar(pm)
    request = create_training_submission(
        train_range={"start": "2026-07-01", "end": "2026-07-02"}, model="ridge_regression",
        validation_sessions=validation_sessions,
        dataset={"feature_group": "previous_day_daily_l2_v1", "label_group": "next_day_high_return_rank/v1"},
    )
    with pytest.raises(ValueError, match="validation"):
        run_offline_training(models=models, feature_groups=groups, path_manager=pm, submission=request, experiment_id="bad-split")
    assert not list(tmp_path.rglob("*.joblib"))
