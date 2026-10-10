# filepath: tests/workflows/test_backtest.py
"""Fixed-model delivery through actual backtest composition and persisted replay."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src import logs
from src.access import Access, meta
from src.config.backtest_config import BacktestConfig, BacktestMode
from src.config.model_config import FeatureGroupConfig, MissingConfig, ModelPreset, PreprocessingConfig
from src.jobs.requests import BacktestSubmission, create_backtest_submission, create_training_submission
from src.trading.portfolio.constructors.threshold import ThresholdConstructor
from src.trading.portfolio.state import PortfolioState
from src.training.evaluation import evaluate_prediction_record
from src.training.prediction import read_prediction_record, verify_prediction_record
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager
from src.workflows.backtest import run_daily_alpha_backtest
from src.workflows.offline_training import run_offline_training


def _decision(day: str) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(time(15, 0), date.fromisoformat(day))


@pytest.fixture
def backtest_log(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "job.log"
    sink = logs.add(
        path, format="{message}", level="INFO",
        filter=lambda record: record["name"] == "src.workflows.backtest"
        or record["name"].startswith("src.trading."),
    )
    try:
        yield path
    finally:
        logs.remove(sink)


def _logged_parameters(log_path: Path, *, experiment_name: str) -> dict[str, object]:
    lines = log_path.read_text().splitlines()
    assert lines[0] == f"▶️ 回测任务; experiment={experiment_name}"
    assert sum(line.startswith("▶️ 回测任务;") for line in lines) == 1
    parameters = {}
    for line in lines[1:7]:
        key, value = line.split(" ", 1)[1].split("=", 1)
        parameters[key] = json.loads(value)
    return parameters


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
    monkeypatch.setattr("src.workflows.backtest.monotonic", lambda: 0.0)
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
        strategy={"type": "threshold", "params": {"threshold": -1.0}},
    )
    return pm, submission, access


@pytest.mark.parametrize("mode", list(BacktestMode))
def test_fixed_model_closes_parameters_prediction_evaluation_and_replay(
    delivery: tuple[PathManager, BacktestSubmission, Mock], backtest_log: Path, mode: BacktestMode,
) -> None:
    pm, submission, _ = delivery
    submission = create_backtest_submission(
        mode=mode, start=submission.start, end=submission.end, model_ref=submission.model_ref,
        strategy={"type": "threshold", "params": {"threshold": -1.123456789}},
    )
    original_model = submission.model_ref.read_bytes()
    run_daily_alpha_backtest(
        backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120), path_manager=pm,
        submission=submission, experiment_id="verified",
    )
    name = "backtest_2026-07-03_2026-07-07_verified"
    run_dir = pm.experiment_dir(experiment_name=name)
    assert {path.name for path in run_dir.iterdir()} == {
        "predictions", "execution.parquet", "equity.parquet", "metrics.json", "report.html",
    }
    assert not (run_dir / "binding.json").exists()
    metrics = json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())
    assert metrics["parameters"] == {
        "mode": mode.value,
        "start": "2026-07-03",
        "end": "2026-07-07",
        "model_ref": str(submission.model_ref),
        "strategy": {
            "type": "threshold", "params": {"threshold": -1.123456789, "target_quantity": 100},
        },
        "backtest_config": {"init_cash": 200_000, "min_listing_calendar_days": 120},
    }
    assert _logged_parameters(backtest_log, experiment_name=name) == metrics["parameters"]
    execution = pq.read_table(run_dir / "execution.parquet").to_pylist()
    fills = [row for row in execution if row["event"] == "FILL"]
    assert len(fills) == 2
    assert sum(row["qty"] for row in fills) == 200
    assert {row["symbol"] for row in fills} == {"000001", "600000"}
    assert float(sum(Decimal(row["commission"]) for row in fills)) == metrics["cost_commission"]
    equity = pq.read_table(run_dir / "equity.parquet").to_pylist()
    assert [point["ts_us"] for point in equity] == [_decision("2026-07-03"), _decision("2026-07-06")]
    assert equity[-1]["cash"] == metrics["final_cash"]
    assert equity[-1]["equity"] == metrics["final_equity"]
    assert equity[-1]["market_value"] == metrics["final_market_value"]
    evaluation = metrics["model_evaluation"]
    assert evaluation["summary"]["date_count"] == 2
    for index, day in enumerate(("2026-07-03", "2026-07-06")):
        prediction_ref = run_dir / "predictions" / f"{day}.parquet"
        inputs, frame = read_prediction_record(prediction_ref)
        assert inputs.model_ref == submission.model_ref
        assert inputs.decision_ts_us == _decision(day)
        assert evaluation["observations"][index]["prediction_ref"] == str(prediction_ref)
        assert frame["symbol"].tolist() == ["600000", "000002", "000001"]
        assert frame["prediction"].isna().tolist() == [False, True, False]
        verify_prediction_record(prediction_ref, pm=pm)
        assert evaluate_prediction_record(prediction_ref, pm=pm).model_dump(mode="json") == evaluation["observations"][index]
    assert submission.model_ref.read_bytes() == original_model
    report = pm.experiment_backtest_report(experiment_name=name).read_text()
    assert "model_evaluation" in report
    assert "parameters" in report
    assert mode.value in report
    assert "-1.123456789" in report
    output = backtest_log.read_text()
    assert "✅ 回测报告;" in output
    assert "决策日期：2026-07-03 ~ 2026-07-06（2 个交易日）" in output
    assert "模型目标 Rank IC：" in output
    assert "次日原始收盘收益 Rank IC：" in output
    assert "完全成交 2 笔" in output
    assert "有效评分 4 行" in output
    assert "backtest_metrics=" not in output
    assert "observations" not in output
    assert "decision_ts_us" not in output
    assert "feature diagnostics" not in output
    assert "score diagnostics" not in output
    assert "backtest prediction" not in output
    assert "full backtest;" not in output
    assert "⏳ 回测推进;" not in output
    assert len(output.splitlines()) < 40


def test_topk_parameters_include_resolved_defaults(
    delivery: tuple[PathManager, BacktestSubmission, Mock], backtest_log: Path,
) -> None:
    pm, submission, _ = delivery
    submission = create_backtest_submission(
        mode=submission.mode, start=submission.start, end=submission.end, model_ref=submission.model_ref,
        strategy={
            "type": "topk_hysteresis",
            "params": {"max_positions": 2, "entry_threshold": -0.1, "exit_threshold": -0.2},
        },
    )
    run_daily_alpha_backtest(
        backtest_config=BacktestConfig(init_cash=300_000, min_listing_calendar_days=90),
        path_manager=pm, submission=submission, experiment_id="topk",
    )
    name = "backtest_2026-07-03_2026-07-07_topk"
    parameters = json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())["parameters"]
    assert parameters["strategy"] == {
        "type": "topk_hysteresis",
        "params": {
            "max_positions": 2, "entry_threshold": -0.1, "exit_threshold": -0.2,
            "rebalance_interval_minutes": 1, "keep_winners": False, "target_quantity": 100,
        },
    }
    assert parameters["backtest_config"] == {"init_cash": 300_000, "min_listing_calendar_days": 90}
    assert _logged_parameters(backtest_log, experiment_name=name) == parameters


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
    run_dir = pm.experiment_dir(experiment_name=name)
    frame = pd.read_parquet(run_dir / "predictions/2026-07-06.parquet")
    assert len(frame) == 3
    assert frame["prediction"].isna().all()
    metrics = json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())
    observation = metrics["model_evaluation"]["observations"][1]
    assert observation["input_count"] == observation["no_prediction_count"] == 3
    assert observation["value"] is None
    assert observation["undefined_reasons"] == ["insufficient_pairs"]
    assert metrics["full_backtest"]["final_positions"] == 2


@pytest.mark.parametrize("maturity", [_decision("2026-07-03") + 1, _decision("2026-07-06")])
def test_unmatured_training_labels_stop_before_predictions(
    delivery: tuple[PathManager, BacktestSubmission, Mock], maturity: int,
) -> None:
    pm, submission, _ = delivery
    schedule = submission.model_ref.with_name("schedule.json")
    record = json.loads(schedule.read_text())
    record["label_maturity_ts_us"] = maturity
    schedule.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="label maturity"):
        run_daily_alpha_backtest(
            backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120), path_manager=pm,
            submission=submission, experiment_id="invalid",
        )
    run_dir = pm.experiment_dir(experiment_name="backtest_2026-07-03_2026-07-07_invalid")
    assert not run_dir.exists()


@pytest.mark.parametrize("maturity", [_decision("2026-07-03") - 1, _decision("2026-07-03")])
def test_training_labels_mature_by_first_decision_can_be_used(
    delivery: tuple[PathManager, BacktestSubmission, Mock], maturity: int,
) -> None:
    pm, submission, _ = delivery
    schedule = submission.model_ref.with_name("schedule.json")
    record = json.loads(schedule.read_text())
    record["label_maturity_ts_us"] = maturity
    schedule.write_text(json.dumps(record))
    run_daily_alpha_backtest(
        backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120),
        path_manager=pm, submission=submission, experiment_id="mature",
    )

    name = "backtest_2026-07-03_2026-07-07_mature"
    inputs, _ = read_prediction_record(
        pm.experiment_dir(experiment_name=name) / "predictions/2026-07-03.parquet",
    )
    assert inputs.decision_ts_us == _decision("2026-07-03")
    assert pm.experiment_backtest_metrics(experiment_name=name).is_file()


def test_start_on_closed_day_uses_first_selected_trading_day(
    delivery: tuple[PathManager, BacktestSubmission, Mock],
) -> None:
    pm, submission, _ = delivery
    submission = replace(submission, start="2026-07-04")
    run_daily_alpha_backtest(
        backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120),
        path_manager=pm, submission=submission, experiment_id="closed-start",
    )

    name = "backtest_2026-07-04_2026-07-07_closed-start"
    inputs, _ = read_prediction_record(
        pm.experiment_dir(experiment_name=name) / "predictions/2026-07-06.parquet",
    )
    assert inputs.decision_ts_us == _decision("2026-07-06")
    metrics = json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())
    assert metrics["parameters"]["start"] == "2026-07-04"
    assert metrics["parameters"]["end"] == "2026-07-07"
    assert metrics["start_date"] == "2026-07-06"


def test_missing_training_record_logs_parameters_without_publishing_results(
    delivery: tuple[PathManager, BacktestSubmission, Mock], backtest_log: Path,
) -> None:
    pm, submission, _ = delivery
    submission.model_ref.with_name("schedule.json").unlink()
    with pytest.raises(FileNotFoundError, match="schedule.json"):
        run_daily_alpha_backtest(
            backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120),
            path_manager=pm, submission=submission, experiment_id="missing-record",
        )

    name = "backtest_2026-07-03_2026-07-07_missing-record"
    assert not pm.experiment_dir(experiment_name=name).exists()
    parameters = _logged_parameters(backtest_log, experiment_name=name)
    assert parameters["model_ref"] == str(submission.model_ref)
    assert parameters["start"] == submission.start
    assert parameters["end"] == submission.end
    assert parameters["mode"] == submission.mode.value
    assert parameters["strategy"] == submission.strategy.model_dump(mode="json")
    assert parameters["backtest_config"] == {"init_cash": 200_000, "min_listing_calendar_days": 120}
    assert "✅ 模型加载;" not in backtest_log.read_text()


def test_model_load_failure_cannot_publish_results(
    delivery: tuple[PathManager, BacktestSubmission, Mock], backtest_log: Path,
) -> None:
    pm, submission, _ = delivery
    submission.model_ref.unlink()
    with pytest.raises(FileNotFoundError):
        run_daily_alpha_backtest(
            backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120), path_manager=pm,
            submission=submission, experiment_id="missing",
        )
    name = "backtest_2026-07-03_2026-07-07_missing"
    assert not pm.experiment_dir(experiment_name=name).exists()
    assert _logged_parameters(backtest_log, experiment_name=name)["model_ref"] == str(submission.model_ref)
    assert "✅ 模型加载;" not in backtest_log.read_text()


@pytest.mark.parametrize("failure", ["calendar", "prices"])
def test_failed_backtest_keeps_parameter_log_without_empty_metrics(
    delivery: tuple[PathManager, BacktestSubmission, Mock], backtest_log: Path, failure: str,
) -> None:
    pm, submission, access = delivery
    if failure == "calendar":
        access.trade_dates.side_effect = RuntimeError("source unavailable")
    else:
        original_read = access.daily_bars.side_effect

        def read_prices(*, trade_date: str, **kwargs: object) -> pd.DataFrame:
            if trade_date == "2026-07-06":
                raise RuntimeError("source unavailable")
            return original_read(trade_date=trade_date, **kwargs)

        access.daily_bars.side_effect = read_prices
    with pytest.raises(RuntimeError, match="source unavailable"):
        run_daily_alpha_backtest(
            backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120),
            path_manager=pm, submission=submission, experiment_id="unavailable",
        )
    name = "backtest_2026-07-03_2026-07-07_unavailable"
    assert _logged_parameters(backtest_log, experiment_name=name)["start"] == submission.start
    assert not pm.experiment_backtest_metrics(experiment_name=name).exists()
    assert not pm.experiment_backtest_report(experiment_name=name).exists()
    run_dir = pm.experiment_dir(experiment_name=name)
    assert not (run_dir / "binding.json").exists()
    assert not (run_dir / "execution.parquet").exists()
    assert not (run_dir / "equity.parquet").exists()
    if failure == "prices":
        assert (run_dir / "predictions/2026-07-03.parquet").exists()


def test_fact_publish_failure_keeps_prior_files_and_stops_metrics_and_report(
    delivery: tuple[PathManager, BacktestSubmission, Mock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, submission, _ = delivery

    def write_facts(*, output_file: Path, table: pa.Table) -> None:
        if output_file.name == "equity.parquet":
            raise OSError("equity storage unavailable")
        write_parquet_atomic(output_file=output_file, table=table)

    monkeypatch.setattr("src.trading.backtest.steps.facts_persist.write_parquet_atomic", write_facts)
    config = BacktestConfig(init_cash=200_000, min_listing_calendar_days=120)
    with pytest.raises(OSError, match="equity storage unavailable"):
        run_daily_alpha_backtest(
            backtest_config=config, path_manager=pm, submission=submission, experiment_id="write-error",
        )
    run_dir = pm.experiment_dir(experiment_name="backtest_2026-07-03_2026-07-07_write-error")
    assert {path.name for path in run_dir.iterdir()} == {"predictions", "execution.parquet"}
    assert len(list((run_dir / "predictions").iterdir())) == 2
    assert pq.read_table(run_dir / "execution.parquet").num_rows == 4
    original_execution = (run_dir / "execution.parquet").read_bytes()
    with pytest.raises(FileExistsError, match="experiment already exists"):
        run_daily_alpha_backtest(
            backtest_config=config, path_manager=pm, submission=submission, experiment_id="write-error",
        )
    assert (run_dir / "execution.parquet").read_bytes() == original_execution


@pytest.mark.parametrize(("change", "message"), [
    ("model_ref", "model_ref from the training record"),
    ("train_dates", "train_dates disagree"),
    ("dataset", "dataset disagrees"),
    ("unordered_dates", "unique and increasing"),
    ("missing_maturity", "label_maturity_ts_us"),
])
def test_invalid_training_record_cannot_publish_results(
    delivery: tuple[PathManager, BacktestSubmission, Mock], change: str, message: str,
) -> None:
    pm, submission, _ = delivery
    schedule = submission.model_ref.with_name("schedule.json")
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
    assert not pm.experiment_dir(experiment_name=name).exists()


def test_backtest_consumes_resolved_record_without_parsing_archived_request(
    delivery: tuple[PathManager, BacktestSubmission, Mock],
) -> None:
    pm, submission, _ = delivery
    schedule = submission.model_ref.with_name("schedule.json")
    record = json.loads(schedule.read_text())
    record["request"] = {"archived_intent": "not a current submission"}
    schedule.write_text(json.dumps(record))
    run_daily_alpha_backtest(
        backtest_config=BacktestConfig(init_cash=200_000, min_listing_calendar_days=120),
        path_manager=pm, submission=submission, experiment_id="resolved-record",
    )
    name = "backtest_2026-07-03_2026-07-07_resolved-record"
    metrics = json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())
    assert metrics["parameters"]["model_ref"] == record["model_ref"]
    assert metrics["model_evaluation"]["summary"]["date_count"] == 2
    assert "training_record" not in metrics["parameters"]


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
    before = pm.experiment_dir(experiment_name="backtest_2026-07-03_2026-07-07_original")
    after = pm.experiment_dir(experiment_name="backtest_2026-07-03_2026-07-07_changed")
    assert (before / "predictions/2026-07-03.parquet").read_bytes() == (after / "predictions/2026-07-03.parquet").read_bytes()
    assert targets[0] == targets[2]
