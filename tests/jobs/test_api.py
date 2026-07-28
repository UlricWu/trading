# filepath: tests/jobs/test_api.py
"""HTTP contract tests for the process-local Job API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest

from src.jobs.api import create_app
from src.jobs.requests import (
    BacktestSubmission,
    DataSubmission,
    JobSubmission,
    TrainingSubmission,
)
from src.jobs.runtime import (
    DataJobScope,
    JobNotFoundError,
    JobRuntime,
    JobSnapshot,
    JobStatus,
    RangeJobScope,
)


class _StubRuntime:
    def __init__(self) -> None:
        self.jobs: dict[str, JobSnapshot] = {}
        self.submitted: list[JobSubmission] = []

    def submit(self, submissions: Sequence[JobSubmission]) -> list[JobSnapshot]:
        snapshots: list[JobSnapshot] = []
        for submission in submissions:
            job_id = f"00000000-0000-4000-8000-{len(self.jobs) + 1:012d}"
            if isinstance(submission, DataSubmission):
                scope = DataJobScope(date=submission.date)
            elif isinstance(submission, (TrainingSubmission, BacktestSubmission)):
                scope = RangeJobScope(
                    start=submission.start,
                    end=submission.end,
                )
            else:
                raise TypeError("unknown submission")
            snapshot = JobSnapshot(
                job_id=job_id,
                kind=submission.kind,
                scope=scope,
                status=JobStatus.PENDING,
                submitted_at="2026-07-20T09:30:00.000000+08:00",
                started_at=None,
                finished_at=None,
            )
            self.jobs[job_id] = snapshot
            snapshots.append(snapshot)
        self.submitted.extend(submissions)
        return snapshots

    def get(self, job_id: str) -> JobSnapshot:
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise JobNotFoundError(job_id) from exc

    def cancel(self, job_id: str) -> JobSnapshot:
        snapshot = self.get(job_id)
        cancelled = JobSnapshot(
            job_id=snapshot.job_id,
            kind=snapshot.kind,
            scope=snapshot.scope,
            status=JobStatus.CANCELLED,
            submitted_at=snapshot.submitted_at,
            started_at=snapshot.started_at,
            finished_at="2026-07-20T09:31:00.000000+08:00",
        )
        self.jobs[job_id] = cancelled
        return cancelled


def test_route_map_contains_only_the_confirmed_endpoints() -> None:
    runtime = _StubRuntime()
    app = create_app(cast(JobRuntime, runtime))

    assert {
        (
            rule.rule,
            tuple(sorted(set(rule.methods or ()) - {"HEAD", "OPTIONS"})),
        )
        for rule in app.url_map.iter_rules()
    } == {
        ("/jobs", ("POST",)),
        ("/jobs/<job_id>", ("GET",)),
        ("/jobs/<job_id>/cancel", ("POST",)),
        ("/health", ("GET",)),
    }


def test_data_range_returns_independent_jobs_and_only_public_fields() -> None:
    runtime = _StubRuntime()
    app = create_app(cast(JobRuntime, runtime))
    app.config["TESTING"] = True

    response = app.test_client().post(
        "/jobs",
        json={
            "kind": "data-standard",
            "start": "2026-07-20",
            "end": "2026-07-21",
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert set(payload) == {"jobs"}
    assert [job["scope"] for job in payload["jobs"]] == [
        {"date": "2026-07-20"},
        {"date": "2026-07-21"},
    ]
    assert set(payload["jobs"][0]) == {
        "job_id",
        "kind",
        "scope",
        "status",
        "submitted_at",
        "started_at",
        "finished_at",
    }


def test_training_request_creates_one_full_range_job() -> None:
    runtime = _StubRuntime()
    app = create_app(cast(JobRuntime, runtime))
    app.config["TESTING"] = True

    response = app.test_client().post(
        "/jobs",
        json={
            "kind": "train",
            "start": "2026-07-01",
            "end": "2026-07-20",
        },
    )

    assert response.status_code == 201
    assert response.get_json()["jobs"][0]["scope"] == {
        "start": "2026-07-01",
        "end": "2026-07-20",
    }
    assert len(runtime.submitted) == 1
    assert isinstance(runtime.submitted[0], TrainingSubmission)


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
            "start": "2026-07-01",
            "end": "2026-07-20",
            "experiment_id": "client-owned",
        },
        {
            "kind": "backtest",
            "mode": "full_backtest",
            "start": "2026-07-01",
            "end": "2026-07-20",
            "model_experiment": "training-1",
            "strategy": {
                "type": "threshold",
                "params": {"threshold": 0.5, "secret": "do-not-echo"},
            },
        },
    ],
)
def test_invalid_request_creates_no_job_and_does_not_echo_payload(
    payload: dict[str, object],
) -> None:
    runtime = _StubRuntime()
    app = create_app(cast(JobRuntime, runtime))
    app.config["TESTING"] = True

    response = app.test_client().post("/jobs", json=payload)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_job_request"
    assert "do-not-echo" not in response.get_data(as_text=True)
    assert runtime.submitted == []


def test_missing_job_response_does_not_echo_requested_identifier() -> None:
    runtime = _StubRuntime()
    app = create_app(cast(JobRuntime, runtime))
    app.config["TESTING"] = True
    identifier = "caller-private-identifier"

    response = app.test_client().get(f"/jobs/{identifier}")

    assert response.status_code == 404
    assert response.get_json() == {
        "error": {
            "code": "job_not_found",
            "message": "job not found",
        }
    }
    assert identifier not in response.get_data(as_text=True)


def test_job_runtime_failure_does_not_change_health_response() -> None:
    class ClosedRuntime(_StubRuntime):
        def submit(
            self,
            submissions: Sequence[JobSubmission],
        ) -> list[JobSnapshot]:
            raise RuntimeError("job runtime is closed")

    app = create_app(cast(JobRuntime, ClosedRuntime()))
    client = app.test_client()

    failed_submission = client.post(
        "/jobs",
        json={"kind": "data-standard", "date": "2026-07-20"},
    )
    health = client.get("/health")

    assert failed_submission.status_code == 500
    assert failed_submission.get_json() == {
        "error": {
            "code": "internal_error",
            "message": "internal server error",
        }
    }
    assert health.status_code == 200
    assert health.get_json() == {"ok": True}
