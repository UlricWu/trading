# filepath: tests/jobs/test_requests.py
"""Submission construction and CLI command contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config.backtest_config import BacktestMode
from src.jobs.requests import (
    BacktestSubmission,
    DataSubmission,
    FeatureBackfillSubmission,
    InvalidJobRequest,
    Level2MinuteBackfillSubmission,
    StandardFactBootstrapSubmission,
    Stock1430BackfillSubmission,
    Stock1430FusionBackfillSubmission,
    TrainingSubmission,
    build_cli_command,
    create_feature_backfill_submission,
    create_level2_minute_backfill_submission,
    create_standard_fact_bootstrap_submission,
    create_stock_1430_backfill_submission,
    create_stock_1430_fusion_backfill_submission,
    parse_job_request,
)



def _training_request() -> dict[str, object]:
    return {
        "kind": "train",
        "train_range": {"start": "2026-07-01", "end": "2026-07-20"},
        "model": "sgd_regression",
        "dataset": {
            "feature_group": "daily_basic_selected_v1",
            "label_group": "daily_close_return_rank_d1/v1",
        },
    }

def test_data_range_creates_one_atomic_submission() -> None:
    submissions = parse_job_request(
        {
            "kind": "data-standard",
            "start": "2026-07-18",
            "end": "2026-07-20",
        }
    )

    assert submissions == [
        DataSubmission(
            kind="data-standard",
            start="2026-07-18",
            end="2026-07-20",
        ),
    ]


def test_training_range_creates_one_submission() -> None:
    submissions = parse_job_request(
        {
            **_training_request(),
        }
    )

    assert len(submissions) == 1 and isinstance(submissions[0], TrainingSubmission)
    assert submissions[0].start == "2026-07-01" and submissions[0].end == "2026-07-20"
    assert submissions[0].train_range.model_dump() == {"start": "2026-07-01", "end": "2026-07-20"}
    command = build_cli_command(submissions[0], "run-1")
    assert command[command.index("--train-start") + 1] == "2026-07-01"
    assert command[command.index("--train-end") + 1] == "2026-07-20"
    assert command[command.index("--model") + 1] == "sgd_regression"
    assert command[command.index("--feature-group") + 1] == "daily_basic_selected_v1"
    assert command[command.index("--label-group") + 1] == "daily_close_return_rank_d1/v1"
    assert "--eval-start" not in command and "--windows-json" not in command
    assert command[command.index("--experiment-id") + 1] == "run-1"


def test_cli_only_data_ranges_create_distinct_submissions() -> None:
    assert create_standard_fact_bootstrap_submission(
        "2019-01-01",
        "2019-04-03",
    ) == StandardFactBootstrapSubmission(
        start="2019-01-01",
        end="2019-04-03",
    )
    assert create_feature_backfill_submission(
        feature_set="tushare_daily_basic",
        version="v1",
        start="2019-04-04",
        end="2019-07-05",
    ) == FeatureBackfillSubmission(
        feature_set="tushare_daily_basic",
        version="v1",
        start="2019-04-04",
        end="2019-07-05",
    )
    assert create_level2_minute_backfill_submission(
        "2025-11-18",
        "2025-11-19",
    ) == Level2MinuteBackfillSubmission(
        start="2025-11-18",
        end="2025-11-19",
    )
    assert create_stock_1430_backfill_submission(
        "2026-05-06",
        "2026-05-07",
    ) == Stock1430BackfillSubmission(
        start="2026-05-06",
        end="2026-05-07",
    )


@pytest.mark.parametrize(
    "kind",
    [
        "data-calendar",
        "data-standard-bootstrap",
        "data-feature-backfill",
        "data-level2-minute-backfill",
        "data-stock-1430-backfill",
        "data-stock-1430-fusion-backfill",
    ],
)
def test_cli_only_data_kinds_are_not_http_job_kinds(kind: str) -> None:
    with pytest.raises(InvalidJobRequest, match="not supported"):
        parse_job_request(
            {
                "kind": kind,
                "start": "2019-01-01",
                "end": "2019-07-01",
            }
        )


def test_fusion_backfill_constructs_a_distinct_submission() -> None:
    assert create_stock_1430_fusion_backfill_submission(
        "2026-05-06", "2026-05-07"
    ) == Stock1430FusionBackfillSubmission(start="2026-05-06", end="2026-05-07")


@pytest.mark.parametrize(
    ("start", "end", "field"),
    (
        ("2026-5-6", "2026-05-07", "start"),
        ("2026-05-06", None, "end"),
        ("2026-05-07", "2026-05-06", "start"),
    ),
)
def test_fusion_backfill_rejects_invalid_ranges(
    start: object, end: object, field: str
) -> None:
    with pytest.raises(InvalidJobRequest) as caught:
        create_stock_1430_fusion_backfill_submission(start, end)
    assert caught.value.field == field


@pytest.mark.parametrize("mode", list(BacktestMode))
def test_backtest_command_preserves_mode_and_uses_job_id_as_experiment_id(
    mode: BacktestMode,
) -> None:
    submission = parse_job_request(
        {
            "kind": "backtest",
            "mode": mode.value,
            "start": "2026-07-01",
            "end": "2026-07-20",
            "model_ref": "/data/models/training-1.joblib",
            "strategy": {
                "type": "threshold",
                "params": {"threshold": 0.5},
            },
        }
    )[0]
    assert isinstance(submission, BacktestSubmission)
    assert submission.mode is mode

    command = build_cli_command(
        submission,
        "00000000-0000-4000-8000-000000000001",
        python_executable=Path("/candidate/python"),
    )

    experiment_index = command.index("--experiment-id")
    assert command[experiment_index + 1] == ("00000000-0000-4000-8000-000000000001")
    assert command[:4] == (
        "/candidate/python",
        "-m",
        "src.cli",
        "backtest",
    )
    assert command[command.index("--mode") + 1] == mode.value
    assert command[command.index("--start") + 1] == "2026-07-01"
    assert command[command.index("--end") + 1] == "2026-07-20"
    assert "--model-use-json" not in command
    assert json.loads(command[command.index("--strategy-json") + 1]) == (
        submission.strategy.model_dump(mode="json")
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "data-standard"},
        {
            "kind": "data-standard",
            "date": "2026-07-20",
            "start": "2026-07-20",
            "end": "2026-07-20",
        },
        {
            "kind": "train",
            "start": "2026-07-20",
            "end": "2026-07-01",
        },
        {
            "kind": "backtest",
            "mode": "unknown",
            "start": "2026-07-01",
            "end": "2026-07-20",
            "model_ref": "/data/models/training-1.joblib",
            "strategy": {
                "type": "threshold",
                "params": {"threshold": 0.5},
            },
        },
    ],
)
def test_invalid_request_is_rejected_before_any_submission(
    payload: dict[str, object],
) -> None:
    with pytest.raises(InvalidJobRequest):
        parse_job_request(payload)


@pytest.mark.parametrize(("field", "value", "error_field"), [
    ("train_range", None, "train_range"),
    ("train_range", {}, "train_range.start"),
    ("train_range", {"start": "2026-7-1", "end": "2026-07-20"}, "train_range.start"),
    ("train_range", {"start": "2026-02-30", "end": "2026-07-20"}, "train_range.start"),
    ("train_range", {"start": "2026-07-21", "end": "2026-07-20"}, "train_range"),
    ("train_range", {"start": True, "end": "2026-07-20"}, "train_range.start"),
    ("train_range", {"start": "2026-07-01", "end": "2026-07-20", "train_dates": []}, "train_range.train_dates"),
    ("eval_range", {"start": "2026-08-01"}, "eval_range.end"),
    ("eval_range", {}, "eval_range.start"),
    ("model", "unknown", "model"),
    ("model", None, "model"),
    ("dataset", {"feature_group": "unknown", "label_group": "daily_close_return_rank_d1/v1"}, "dataset.feature_group"),
    ("dataset", {"feature_group": "daily_basic_selected_v1", "label_group": "daily_close_return_rank_d7/v1"}, "dataset.label_group"),
    ("dataset", {"feature_group": "daily_basic_selected_v1", "label_group": "l2_stock_1430_t1_vwap_rank/v1"}, "dataset"),
    ("dataset", {"feature_group": "l2_stock_1430_all_v1", "label_group": "daily_close_return_rank_d1/v1"}, "dataset"),
    ("dataset", {"feature_group": "stock_1430_daily_l2/v1", "label_group": "l2_stock_1430_t1_vwap_rank/v1"}, "dataset.feature_group"),
])
def test_training_request_rejects_invalid_ranges_and_choices(
    field: str, value: object, error_field: str,
) -> None:
    payload = _training_request()
    payload[field] = value
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == error_field


@pytest.mark.parametrize("field", [
    "windows", "train_dates", "training_cutoff_ts_us", "label_availability",
    "decision_ts_us", "availability_ref", "experiment_id", "model_params",
])
def test_training_rejects_removed_technical_inputs(field: str) -> None:
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request({**_training_request(), field: "not a business choice"})
    assert caught.value.field == field


@pytest.mark.parametrize("end", ["2026-08-10", "2026-08-31"])
def test_single_and_multiple_evaluation_days_share_one_request_shape(end: str) -> None:
    submission = parse_job_request({
        **_training_request(), "eval_range": {"start": "2026-08-10", "end": end},
    })[0]
    assert isinstance(submission, TrainingSubmission)
    assert submission.start == "2026-07-01" and submission.end == end
    command = build_cli_command(submission, "run-1")
    assert command[command.index("--eval-start") + 1] == "2026-08-10"
    assert command[command.index("--eval-end") + 1] == end


def test_omitted_and_null_evaluation_both_mean_pure_training() -> None:
    omitted = parse_job_request(_training_request())[0]
    explicit = parse_job_request({**_training_request(), "eval_range": None})[0]
    assert omitted == explicit


@pytest.mark.parametrize("ref", ["training-run", "relative/model.joblib", "../model.joblib", None, 1])
def test_backtest_requires_a_concrete_absolute_model_reference(ref: object) -> None:
    from src.jobs.requests import create_backtest_submission
    with pytest.raises(InvalidJobRequest) as caught:
        create_backtest_submission(
            mode="signal_eval", start="2026-07-01", end="2026-07-02", model_ref=ref,
            strategy={"type": "threshold", "params": {"threshold": 0.5}},
        )
    assert caught.value.field == "model_ref"


def test_training_scope_covers_both_ranges_before_runtime_information_checks() -> None:
    submission = parse_job_request({
        **_training_request(), "train_range": {"start": "2026-07-20", "end": "2026-07-20"},
        "eval_range": {"start": "2026-07-02", "end": "2026-07-02"},
    })[0]
    assert (submission.start, submission.end) == ("2026-07-02", "2026-07-20")


def test_training_remains_one_job_with_explicit_gap() -> None:
    submissions = parse_job_request({**_training_request(), "eval_gap_sessions": 1, "validation_sessions": 27})
    assert len(submissions) == 1
    command = build_cli_command(submissions[0], "one-job")
    assert command[command.index("--eval-gap-sessions") + 1] == "1"
    assert command[command.index("--validation-sessions") + 1] == "27"


@pytest.mark.parametrize("field", ["recipe", "combinations", "retrain", "eval_ranges"])
def test_train_rejects_recipe_and_multi_job_expansion(field: str) -> None:
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request({**_training_request(), field: []})
    assert caught.value.field == field


@pytest.mark.parametrize("gap", [-1, True, 1.5, "1"])
@pytest.mark.parametrize("field", ["eval_gap_sessions", "validation_sessions"])
def test_session_counts_must_be_nonnegative_integers(gap: object, field: str) -> None:
    with pytest.raises(InvalidJobRequest):
        parse_job_request({**_training_request(), field: gap})
