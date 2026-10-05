# filepath: tests/workflows/test_offline_training.py
import json
from datetime import date, time
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import numpy as np
import pytest

from src.access import meta
from src.config.model_config import FeatureLabelConfig, MissingConfig, ModelConfig, PreprocessingConfig
from src.jobs.requests import create_training_submission
from src.training.artifact import load
from src.training.evaluation import load_evaluation
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager
from src.workflows.offline_training import run_offline_training


def _ts(day: str, hour: int = 15, minute: int = 10) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(time(hour, minute), date.fromisoformat(day))


def _calendar(pm: PathManager) -> None:
    dates = pd.date_range("2026-01-01", "2026-12-31")
    path = pm.processed_year_object(dataset_name="trade_calendar", version="v1", calendar_year=2026).payload_path
    path.parent.mkdir(parents=True)
    pd.DataFrame({"trade_date": dates.strftime("%Y-%m-%d"), "is_open": dates.dayofweek < 5}).to_parquet(path, index=False)
    meta.commit(pm=pm, payload_path=path)


def _config() -> ModelConfig:
    return ModelConfig(group="sgd_regression", model_params={"random_state": 7, "shuffle": False}, dataset=FeatureLabelConfig(
        feature_set="tushare_daily_basic", feature_version="v1", feature_columns=["f_d_close_return_1d"],
        label_set="daily_close_return_rank_d1", label_version="v1", label_column="y_rank_return",
    ))


def _partition(pm: PathManager, day: str) -> None:
    keys = {"symbol": ["000001", "600000"], "trade_date": [day] * 2}
    feature = pm.feature_object(feature_set="tushare_daily_basic", version="v1", trade_date=day).payload_path
    label = pm.label_object(label_set="daily_close_return_rank_d1", version="v1", trade_date=day).payload_path
    for path, column, values in ((feature, "f_d_close_return_1d", [0.0, 0.2]), (label, "y_rank_return", [0.25, 0.75])):
        path.parent.mkdir(parents=True)
        pd.DataFrame({**keys, column: values}).to_parquet(path, index=False)
        meta.commit(pm=pm, payload_path=path)


def _selection(*, cutoff: int | None = None) -> dict[str, object]:
    return {"train_dates": ["2026-07-01"], "training_cutoff_ts_us": _ts("2026-07-02") if cutoff is None else cutoff,
            "label_availability": {"2026-07-01": {"available_ts_us": _ts("2026-07-02"), "evidence_ref": "synthetic/availability-1"}}}


def test_pure_training_needs_no_evaluation_files_and_records_explicit_cutoff(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm)
    _partition(pm, "2026-07-01")
    submission = create_training_submission([_selection()])
    assert run_offline_training(model_config=_config(), path_manager=pm, submission=submission, experiment_id="run-1") is None
    name = "training_2026-07-01_2026-07-01_run-1"
    ref = pm.experiment_training_model(experiment_name=name, window_index=0)
    model = load(ref)
    assert model.train_dates == ("2026-07-01",)
    assert model.label_set == "daily_close_return_rank_d1"
    schedule = json.loads(pm.experiment_training_schedule(experiment_name=name).read_text())
    assert schedule[0]["model_ref"] == str(ref)
    assert schedule[0]["training_cutoff_ts_us"] == _ts("2026-07-02")
    assert list(ref.parent.iterdir()) == [ref]
    assert not pm.experiment_training_metrics(experiment_name=name).exists()
    assert not pm.experiment_training_report(experiment_name=name).exists()


def test_complete_workflow_saves_every_window_then_evaluates_all_refs(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm)
    for day in ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"):
        _partition(pm, day)
    first = {**_selection(), "eval_date": "2026-07-03", "decision_ts_us": _ts("2026-07-03", minute=20)}
    second = {"train_dates": ["2026-07-01", "2026-07-02"], "training_cutoff_ts_us": _ts("2026-07-03"),
              "label_availability": {"2026-07-01": {"available_ts_us": _ts("2026-07-02"), "evidence_ref": "synthetic/availability-1"}, "2026-07-02": {"available_ts_us": _ts("2026-07-03"), "evidence_ref": "synthetic/availability-2"}},
              "eval_date": "2026-07-06", "decision_ts_us": _ts("2026-07-06", minute=20)}
    run_offline_training(model_config=_config(), path_manager=pm, submission=create_training_submission([first, second]), experiment_id="run-2")
    name = "training_2026-07-01_2026-07-06_run-2"
    evaluation = load_evaluation(pm.experiment_training_metrics(experiment_name=name))
    assert evaluation.summary.date_count == 2 and evaluation.summary.defined_date_count == 2
    for index, expected_dates in enumerate((("2026-07-01",), ("2026-07-01", "2026-07-02"))):
        ref = pm.experiment_training_model(experiment_name=name, window_index=index)
        assert load(ref).train_dates == expected_dates
        assert evaluation.observations[index].model_ref == str(ref)
        assert len(pd.read_parquet(ref.parent / "predictions.parquet")) == 2


def test_explicit_unavailable_dates_fail_without_shortening_or_publishing(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm)
    with pytest.raises(ValueError, match="unavailable at training cutoff"):
        run_offline_training(model_config=_config(), path_manager=pm, submission=create_training_submission([_selection(cutoff=_ts("2026-07-02", minute=0))]), experiment_id="fail")
    assert list((tmp_path / "experiments").iterdir()) == []


@pytest.mark.parametrize(("available_minute", "cutoff_minute", "message"), [(30, 30, "precedes maturity"), (36, 30, "unavailable at training cutoff")])
def test_1430_label_cannot_be_used_before_next_session_1436(tmp_path: Path, available_minute: int, cutoff_minute: int, message: str) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm)
    cfg = _config()
    cfg.dataset = FeatureLabelConfig(feature_set="l2_stock_1430", feature_version="v1", feature_columns=["f_l2_notional_rank_5m"], label_set="l2_stock_1430_t1_vwap_rank", label_version="v1", label_column="y_rank_return")
    item = _selection(cutoff=_ts("2026-07-02", 14, cutoff_minute))
    item["label_availability"]["2026-07-01"]["available_ts_us"] = _ts("2026-07-02", 14, available_minute)
    with pytest.raises(ValueError, match=message):
        run_offline_training(model_config=cfg, path_manager=pm, submission=create_training_submission([item]), experiment_id="fail")
    assert list((tmp_path / "experiments").iterdir()) == []


def test_unknown_family_is_rejected_before_calendar_or_experiment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = PathManager(tmp_path)
    cfg = _config()
    cfg.group = "unknown"
    access = Mock(side_effect=AssertionError("Access must not be constructed"))
    monkeypatch.setattr("src.workflows.offline_training.Access", access)
    with pytest.raises(ValueError, match="unsupported model group"):
        run_offline_training(model_config=cfg, path_manager=pm, submission=create_training_submission([_selection()]), experiment_id="fail")
    access.assert_not_called()
    assert list((tmp_path / "experiments").iterdir()) == []


def test_daily_close_features_cannot_be_used_at_1430(tmp_path: Path) -> None:
    item = {**_selection(), "eval_date": "2026-07-03", "decision_ts_us": _ts("2026-07-03", 14, 30)}
    with pytest.raises(ValueError, match="visibility"):
        run_offline_training(model_config=_config(), path_manager=PathManager(tmp_path), submission=create_training_submission([item]), experiment_id="fail")


def test_selected_closed_date_is_rejected_instead_of_ignored(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm)
    item = {"train_dates": ["2026-07-04"], "training_cutoff_ts_us": _ts("2026-07-07"), "label_availability": {"2026-07-04": {"available_ts_us": _ts("2026-07-06"), "evidence_ref": "synthetic/closed-date"}}}
    with pytest.raises(ValueError, match="not an open session"):
        run_offline_training(model_config=_config(), path_manager=pm, submission=create_training_submission([item]), experiment_id="fail")
    assert list((tmp_path / "experiments").iterdir()) == []


def test_independent_workflow_retraining_preserves_fitted_state_and_predictions(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _calendar(pm)
    symbols = ["000001", "000002", "000003", "000004"]
    for day in ("2026-07-01", "2026-07-02"):
        keys = {"symbol": symbols, "trade_date": [day] * 4}
        for ref, column, values in (
            (
                pm.feature_object(feature_set="tushare_daily_basic", version="v1", trade_date=day).payload_path,
                "f_d_close_return_1d", [0.0, np.nan, 2.0, 3.0],
            ),
            (
                pm.label_object(label_set="daily_close_return_rank_d1", version="v1", trade_date=day).payload_path,
                "y_rank_return", [0.0, 0.5, 1.0, 1.5],
            ),
        ):
            ref.parent.mkdir(parents=True)
            pd.DataFrame({**keys, column: values}).to_parquet(ref, index=False)
            meta.commit(pm=pm, payload_path=ref)
    config = _config()
    config.model_params = {"random_state": 7, "alpha": 0.001, "shuffle": False}
    config.preprocessing = PreprocessingConfig(missing=MissingConfig(method="drop"))
    submission = create_training_submission([{
        "train_dates": ["2026-07-01", "2026-07-02"], "training_cutoff_ts_us": _ts("2026-07-03"),
        "label_availability": {
            "2026-07-01": {"available_ts_us": _ts("2026-07-02"), "evidence_ref": "synthetic/1"},
            "2026-07-02": {"available_ts_us": _ts("2026-07-03"), "evidence_ref": "synthetic/2"},
        },
    }])
    models = []
    for run_id in ("first", "second"):
        run_offline_training(model_config=config, path_manager=pm, submission=submission, experiment_id=run_id)
        name = f"training_2026-07-01_2026-07-02_{run_id}"
        models.append(load(pm.experiment_training_model(experiment_name=name, window_index=0)))
    first, second = models
    assert first.preprocess == second.preprocess
    assert first.model is not second.model
    for field in ("coef_", "intercept_"):
        np.testing.assert_array_equal(getattr(first.model, field), getattr(second.model, field), strict=True)
    for field in ("t_", "n_iter_"):
        assert getattr(first.model, field) == getattr(second.model, field)
    inputs = np.array([[0.0], [np.nan], [1.0], [3.0]], dtype=np.float64)
    np.testing.assert_array_equal(first.predict(inputs), second.predict(inputs), strict=True)
