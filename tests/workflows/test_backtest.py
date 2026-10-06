# filepath: tests/workflows/test_backtest.py
"""Fixed-model delivery through actual backtest composition and persisted replay."""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, time
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from src.access import Access, meta
from src.config.backtest_config import BacktestConfig
from src.config.model_config import FeatureGroupConfig, MissingConfig, ModelPreset, PreprocessingConfig
from src.jobs.requests import BacktestSubmission, create_backtest_submission, create_training_submission
from src.trading.portfolio.constructors.threshold import ThresholdConstructor
from src.trading.portfolio.state import PortfolioState
from src.training.evaluation import evaluate_prediction_record
from src.training.prediction import verify_prediction_record
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager
from src.workflows.backtest import run_daily_alpha_backtest
from src.workflows.offline_training import run_offline_training


def _decision(day: str) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(time(15, 0), date.fromisoformat(day))


@pytest.fixture
def delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[PathManager, BacktestSubmission, Mock]:
    pm = PathManager(tmp_path)
    days = ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07")
    access = Mock(spec=Access)
    access.trade_dates.side_effect = lambda *, start_date, end_date: [
        day for day in days if start_date <= day <= end_date
    ]
    access.next_trade_date.side_effect = lambda *, trade_date: days[days.index(trade_date) + 1]
    access.universe.return_value = ("600000", "000002", "000001")
    access.daily_bars.side_effect = lambda **kwargs: pd.DataFrame({
        "symbol": ["000001", "000002", "600000"], "close": [10.0, 20.0, 30.0],
    })
    monkeypatch.setattr("src.workflows.offline_training.Access", Mock(return_value=access))
    monkeypatch.setattr("src.workflows.backtest.Access", Mock(return_value=access))
    for day in ("2026-07-01", "2026-07-03", "2026-07-06"):
        keys = {"symbol": ["000001", "000002", "600000"], "trade_date": [day] * 3}
        feature_ref = pm.feature_object(
            feature_set="tushare_daily_basic", version="v1", trade_date=day,
        ).payload_path
        label_ref = pm.label_object(
            label_set="daily_close_return_rank_d1", version="v1", trade_date=day,
        ).payload_path
        for ref, column, values in (
            (feature_ref, "f_d_close_return_1d", [0.0, np.nan, 2.0]),
            (label_ref, "y_rank_return", [0.25, np.nan, 0.75]),
        ):
            ref.parent.mkdir(parents=True)
            pd.DataFrame({**keys, column: values}).to_parquet(ref, index=False)
            meta.commit(pm=pm, payload_path=ref)
    preset = ModelPreset(
        group="sgd_regression", model_params={"random_state": 7},
        preprocessing=PreprocessingConfig(missing=MissingConfig(method="drop")),
    )
    feature = FeatureGroupConfig(
        feature_set="tushare_daily_basic", feature_version="v1",
        feature_columns=["f_d_close_return_1d"],
    )
    run_offline_training(
        models={"sgd_regression": preset}, feature_groups={"daily_basic_selected_v1": feature},
        path_manager=pm, experiment_id="delivery",
        submission=create_training_submission(
            train_range={"start": "2026-07-01", "end": "2026-07-01"},
            model="sgd_regression", dataset={
                "feature_group": "daily_basic_selected_v1",
                "label_group": "daily_close_return_rank_d1/v1",
            },
        ),
    )
    training_name = "training_2026-07-01_2026-07-01_delivery"
    submission = create_backtest_submission(
        mode="signal_eval", start="2026-07-03", end="2026-07-07",
        model_ref=pm.experiment_training_model(experiment_name=training_name),
        model_use={
            "training_schedule_ref": pm.experiment_training_schedule(experiment_name=training_name),
            "effective_ts_us": _decision("2026-07-03"),
            "availability_evidence_ref": "synthetic/conditional-model-availability",
        },
        strategy={"type": "threshold", "params": {"threshold": -1.0}},
    )
    return pm, submission, access


def test_fixed_model_closes_binding_prediction_evaluation_and_replay(
    delivery: tuple[PathManager, BacktestSubmission, Mock],
) -> None:
    pm, submission, _ = delivery
    original_model = submission.model_ref.read_bytes()
    run_daily_alpha_backtest(
        backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120), path_manager=pm,
        submission=submission, experiment_id="verified",
    )
    name = "backtest_2026-07-03_2026-07-07_verified"
    run_dir = pm.experiment_backtest_dir(experiment_name=name)
    binding = json.loads((run_dir / "binding.json").read_text())
    assert binding["model_ref"] == str(submission.model_ref)
    assert binding["model_use"]["effective_ts_us"] == binding["first_decision_ts_us"]
    metrics = json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())
    evaluation = metrics["model_evaluation"]
    assert evaluation["summary"]["date_count"] == 2
    for index, day in enumerate(("2026-07-03", "2026-07-06")):
        prediction_ref = run_dir / "predictions" / f"{day}.parquet"
        frame = pd.read_parquet(prediction_ref)
        assert frame["symbol"].tolist() == ["600000", "000002", "000001"]
        assert frame["prediction"].isna().tolist() == [False, True, False]
        verify_prediction_record(prediction_ref, pm=pm)
        assert evaluate_prediction_record(prediction_ref, pm=pm).model_dump(mode="json") == evaluation["observations"][index]
    assert submission.model_ref.read_bytes() == original_model
    assert "model_evaluation" in pm.experiment_backtest_report(experiment_name=name).read_text()


def test_all_skipped_day_keeps_prediction_rows_and_existing_positions(
    delivery: tuple[PathManager, BacktestSubmission, Mock],
) -> None:
    pm, submission, _ = delivery
    feature_ref = pm.feature_object(
        feature_set="tushare_daily_basic", version="v1", trade_date="2026-07-06",
    ).payload_path
    features = pd.read_parquet(feature_ref)
    features["f_d_close_return_1d"] = np.nan
    features.to_parquet(feature_ref, index=False)
    meta.commit(pm=pm, payload_path=feature_ref)
    run_daily_alpha_backtest(
        backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120),
        path_manager=pm, submission=submission, experiment_id="all-skipped",
    )
    name = "backtest_2026-07-03_2026-07-07_all-skipped"
    run_dir = pm.experiment_backtest_dir(experiment_name=name)
    frame = pd.read_parquet(run_dir / "predictions/2026-07-06.parquet")
    assert len(frame) == 3
    assert frame["prediction"].isna().all()
    metrics = json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())
    observation = metrics["model_evaluation"]["observations"][1]
    assert observation["input_count"] == observation["no_prediction_count"] == 3
    assert observation["value"] is None
    assert observation["undefined_reasons"] == ["insufficient_pairs"]
    assert metrics["full_backtest"]["final_positions"] == 2


@pytest.mark.parametrize("effective", [_decision("2026-07-02") - 1, _decision("2026-07-03") + 1])
def test_invalid_model_use_stops_before_predictions(
    delivery: tuple[PathManager, BacktestSubmission, Mock], effective: int,
) -> None:
    pm, submission, _ = delivery
    submission = replace(submission, model_use=submission.model_use.model_copy(update={"effective_ts_us": effective}))
    with pytest.raises(ValueError, match="label maturity"):
        run_daily_alpha_backtest(
            backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120), path_manager=pm,
            submission=submission, experiment_id="invalid",
        )
    run_dir = pm.experiment_backtest_dir(experiment_name="backtest_2026-07-03_2026-07-07_invalid")
    assert not run_dir.exists()


def test_model_load_failure_cannot_publish_a_binding(
    delivery: tuple[PathManager, BacktestSubmission, Mock],
) -> None:
    pm, submission, _ = delivery
    submission.model_ref.unlink()
    with pytest.raises(FileNotFoundError):
        run_daily_alpha_backtest(
            backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120), path_manager=pm,
            submission=submission, experiment_id="missing",
        )
    run_dir = pm.experiment_backtest_dir(experiment_name="backtest_2026-07-03_2026-07-07_missing")
    assert not (run_dir / "binding.json").exists()


@pytest.mark.parametrize(("change", "message"), [
    ("model_ref", "model_ref from the training record"),
    ("train_dates", "train_dates disagree"),
    ("dataset", "dataset disagrees"),
    ("unordered_dates", "unique and increasing"),
    ("missing_maturity", "label_maturity_ts_us"),
])
def test_invalid_training_record_cannot_publish_a_binding(
    delivery: tuple[PathManager, BacktestSubmission, Mock], change: str, message: str,
) -> None:
    pm, submission, _ = delivery
    schedule = submission.model_use.training_schedule_ref
    record = json.loads(schedule.read_text())
    if change == "model_ref":
        record["model_ref"] = str(submission.model_ref.with_name("another.joblib"))
    elif change == "train_dates":
        record["train_dates"] = ["2026-06-30"]
    elif change == "dataset":
        record["configuration"]["dataset"]["label_set"] = "daily_close_return_rank_d3"
    elif change == "unordered_dates":
        record["train_dates"] = ["2026-07-01", "2026-06-30"]
    else:
        del record["label_maturity_ts_us"]
    schedule.write_text(json.dumps(record))
    with pytest.raises(ValueError, match=message):
        run_daily_alpha_backtest(
            backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120),
            path_manager=pm, submission=submission, experiment_id="invalid-record",
        )
    name = "backtest_2026-07-03_2026-07-07_invalid-record"
    assert not pm.experiment_backtest_dir(experiment_name=name).exists()


def test_backtest_consumes_resolved_record_without_parsing_archived_request(
    delivery: tuple[PathManager, BacktestSubmission, Mock],
) -> None:
    pm, submission, _ = delivery
    schedule = submission.model_use.training_schedule_ref
    record = json.loads(schedule.read_text())
    record["request"] = {"archived_intent": "not a current submission"}
    schedule.write_text(json.dumps(record))
    run_daily_alpha_backtest(
        backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120),
        path_manager=pm, submission=submission, experiment_id="resolved-record",
    )
    name = "backtest_2026-07-03_2026-07-07_resolved-record"
    binding = json.loads((pm.experiment_backtest_dir(experiment_name=name) / "binding.json").read_text())
    assert binding["training_record"] == record
    assert binding["model_ref"] == record["model_ref"]
    assert pm.experiment_backtest_metrics(experiment_name=name).exists()


def test_changed_future_labels_and_prices_cannot_change_prior_predictions_or_targets(
    delivery: tuple[PathManager, BacktestSubmission, Mock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, submission, access = delivery
    targets: list[dict[str, int]] = []
    original_targets = ThresholdConstructor.targets

    def record_targets(
        self: ThresholdConstructor, *, ts_us: int,
        scores: Mapping[str, float], state: PortfolioState,
    ) -> dict[str, int]:
        result = original_targets(self, ts_us=ts_us, scores=scores, state=state)
        targets.append(result.copy())
        return result

    monkeypatch.setattr(ThresholdConstructor, "targets", record_targets)
    for run_id in ("original", "changed"):
        if run_id == "changed":
            label_ref = pm.label_object(
                label_set="daily_close_return_rank_d1", version="v1", trade_date="2026-07-03",
            ).payload_path
            labels = pd.read_parquet(label_ref)
            labels["y_rank_return"] = [np.nan, np.nan, np.nan]
            labels.to_parquet(label_ref, index=False)
            meta.commit(pm=pm, payload_path=label_ref)
            access.daily_bars.side_effect = lambda *, trade_date, **kwargs: pd.DataFrame({
                "symbol": ["000001", "000002", "600000"],
                "close": [10.0, 20.0, 30.0] if trade_date == "2026-07-03" else [1.0, 2.0, 3.0],
            })
        run_daily_alpha_backtest(
            backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120), path_manager=pm,
            submission=submission, experiment_id=run_id,
        )
    before = pm.experiment_backtest_dir(experiment_name="backtest_2026-07-03_2026-07-07_original")
    after = pm.experiment_backtest_dir(experiment_name="backtest_2026-07-03_2026-07-07_changed")
    assert (before / "predictions/2026-07-03.parquet").read_bytes() == (after / "predictions/2026-07-03.parquet").read_bytes()
    assert targets[0] == targets[2]
