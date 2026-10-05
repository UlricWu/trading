# filepath: tests/jobs/test_requests.py
"""Submission construction and CLI command contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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



def _training_windows() -> list[dict[str, object]]:
    return [{
        "train_dates": ["2026-07-01", "2026-07-20"],
        "training_cutoff_ts_us": 1785510000000000,
        "label_availability": {
            "2026-07-01": {"available_ts_us": 1782976200000000, "evidence_ref": "test/label-1"},
            "2026-07-20": {"available_ts_us": 1784617200000000, "evidence_ref": "test/label-20"},
        },
    }]

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
            "kind": "train", "windows": _training_windows(),
        }
    )

    assert len(submissions) == 1 and isinstance(submissions[0], TrainingSubmission)
    assert submissions[0].start == "2026-07-01" and submissions[0].end == "2026-07-20"
    assert submissions[0].windows[0].train_dates == ("2026-07-01", "2026-07-20")
    command = build_cli_command(submissions[0], "run-1")
    import json
    assert json.loads(command[command.index("--windows-json") + 1]) == [window.model_dump(mode="json") for window in submissions[0].windows]
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


def test_backtest_command_uses_job_id_as_experiment_id() -> None:
    submission = parse_job_request(
        {
            "kind": "backtest",
            "mode": "full_backtest",
            "start": "2026-07-01",
            "end": "2026-07-20",
            "model_ref": "/data/models/training-1.joblib",
            "model_use": {'training_schedule_ref': '/data/training/schedule.json', 'effective_ts_us': 1782860400000000, 'availability_evidence_ref': 'synthetic/model-use'},
            "strategy": {
                "type": "threshold",
                "params": {"threshold": 0.5},
            },
        }
    )[0]
    assert isinstance(submission, BacktestSubmission)

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
    model_use_index = command.index("--model-use-json")
    assert json.loads(command[model_use_index + 1]) == submission.model_use.model_dump(mode="json")


@pytest.mark.parametrize("invalid_use", [
    None,
    {},
    {"training_schedule_ref": "relative.json", "effective_ts_us": 1, "availability_evidence_ref": "record"},
    {"training_schedule_ref": "/data/schedule.json", "effective_ts_us": True, "availability_evidence_ref": "record"},
    {"training_schedule_ref": "/data/schedule.json", "effective_ts_us": 1, "availability_evidence_ref": " "},
    {"training_schedule_ref": "/data/schedule.json", "effective_ts_us": 1, "availability_evidence_ref": "record", "extra": 1},
])
def test_backtest_requires_an_unambiguous_model_use_before_submission(invalid_use: object) -> None:
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request({
            "kind": "backtest", "mode": "signal_eval",
            "start": "2026-07-03", "end": "2026-07-06",
            "model_ref": "/data/model.joblib", "model_use": invalid_use,
            "strategy": {"type": "threshold", "params": {"threshold": 0.5}},
        })
    assert caught.value.field == "model_use"


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
            "model_use": {'training_schedule_ref': '/data/training/schedule.json', 'effective_ts_us': 1782860400000000, 'availability_evidence_ref': 'synthetic/model-use'},
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


@pytest.mark.parametrize("mutation", ["empty_dates", "duplicate_dates", "unsorted_dates", "bad_date", "missing_availability", "extra_availability", "boolean_cutoff", "boolean_availability", "empty_evidence", "unpaired_evaluation", "decision_wrong_day", "decision_before_cutoff", "extra_window_field"])
def test_training_schedule_rejects_ambiguous_dates_or_information_inputs(mutation: str) -> None:
    from src.jobs.requests import create_training_submission
    windows = _training_windows()
    item = windows[0]
    if mutation == "empty_dates":
        item["train_dates"] = []
    elif mutation == "duplicate_dates":
        item["train_dates"] = ["2026-07-01", "2026-07-01"]
    elif mutation == "unsorted_dates":
        item["train_dates"] = ["2026-07-20", "2026-07-01"]
    elif mutation == "bad_date":
        item["train_dates"] = ["2026-7-1"]
    elif mutation == "missing_availability":
        item["label_availability"].pop("2026-07-01")
    elif mutation == "extra_availability":
        item["label_availability"]["2026-07-02"] = {"available_ts_us": 1, "evidence_ref": "test"}
    elif mutation == "boolean_cutoff":
        item["training_cutoff_ts_us"] = True
    elif mutation == "boolean_availability":
        item["label_availability"]["2026-07-01"]["available_ts_us"] = True
    elif mutation == "empty_evidence":
        item["label_availability"]["2026-07-01"]["evidence_ref"] = " "
    elif mutation == "unpaired_evaluation":
        item["eval_date"] = "2026-07-20"
    elif mutation == "decision_wrong_day":
        item["eval_date"] = "2026-07-20"
        item["decision_ts_us"] = 1782976200000000
    elif mutation == "decision_before_cutoff":
        item["eval_date"] = "2026-07-02"
        item["decision_ts_us"] = 1782976200000000
    else:
        item["window_days"] = 30
    with pytest.raises(InvalidJobRequest) as caught:
        create_training_submission(windows)
    assert caught.value.field == "windows"


@pytest.mark.parametrize("ref", ["training-run", "relative/model.joblib", "../model.joblib", None, 1])
def test_backtest_requires_a_concrete_absolute_model_reference(ref: object) -> None:
    from src.jobs.requests import create_backtest_submission
    with pytest.raises(InvalidJobRequest) as caught:
        create_backtest_submission(mode="signal_eval", start="2026-07-01", end="2026-07-02", model_ref=ref, model_use={'training_schedule_ref': '/data/training/schedule.json', 'effective_ts_us': 1782860400000000, 'availability_evidence_ref': 'synthetic/model-use'}, strategy={"type": "threshold", "params": {"threshold": 0.5}})
    assert caught.value.field == "model_ref"


def test_training_scope_covers_every_selected_date_before_runtime_maturity_checks() -> None:
    from src.jobs.requests import create_training_submission
    item = {"train_dates": ["2026-07-20"], "training_cutoff_ts_us": 1782976200000000,
            "label_availability": {"2026-07-20": {"available_ts_us": 1782976200000000, "evidence_ref": "synthetic/invalid-history"}},
            "eval_date": "2026-07-02", "decision_ts_us": 1782976200000000}
    submission = create_training_submission([item])
    assert (submission.start, submission.end) == ("2026-07-02", "2026-07-20")
