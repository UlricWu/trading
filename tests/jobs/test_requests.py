# filepath: tests/jobs/test_requests.py
"""Submission construction and CLI command contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.jobs.requests import (
    BacktestSubmission,
    DataSubmission,
    InvalidJobRequest,
    TrainingSubmission,
    build_cli_command,
    parse_job_request,
)


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
            "kind": "train",
            "start": "2026-07-01",
            "end": "2026-07-20",
        }
    )

    assert submissions == [TrainingSubmission(start="2026-07-01", end="2026-07-20")]


def test_backtest_command_uses_job_id_as_experiment_id() -> None:
    submission = parse_job_request(
        {
            "kind": "backtest",
            "mode": "full_backtest",
            "start": "2026-07-01",
            "end": "2026-07-20",
            "model_experiment": "training-1",
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
            "model_experiment": "training-1",
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
