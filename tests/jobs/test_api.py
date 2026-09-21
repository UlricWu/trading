# filepath: tests/jobs/test_api.py
"""HTTP contract tests for the process-local Job API."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock, Mock, call

import pytest
import requests
import tushare as ts

import src.jobs.api as api_module
from src.config.app_config import AppConfig
from src.config.secret_config import SecretConfig
from src.jobs.api import create_app
from src.jobs.requests import (
    BacktestSubmission,
    DataSubmission,
    JobSubmission,
    TrainingSubmission,
)
from src.jobs.runtime import (
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
            if isinstance(
                submission,
                (DataSubmission, TrainingSubmission, BacktestSubmission),
            ):
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
        ("/checks/tushare", ("POST",)),
    }


@pytest.fixture
def tushare_config(monkeypatch: pytest.MonkeyPatch) -> Mock:
    secret = SecretConfig(
        ftp_host="ftp.example.com",
        ftp_user="user",
        ftp_password="password",
        tushare_token="private-tushare-token",
        tushare_gateway="https://private-gateway.example/dataapi",
    )
    load_config = Mock(return_value=Mock(spec=AppConfig, secret=secret))
    monkeypatch.setattr(AppConfig, "load", load_config)
    monkeypatch.setattr(
        api_module.DateTimeUtils,
        "now_utc",
        lambda: datetime(2026, 9, 19, 16, 15, tzinfo=UTC),
    )
    monkeypatch.setattr(
        ts,
        "set_token",
        Mock(side_effect=AssertionError("check must not persist a token")),
    )
    return load_config


@pytest.mark.parametrize(
    ("http_status", "payload", "expected_status"),
    [
        (200, {"code": 0, "data": {"fields": ["close"], "items": [[10.0]]}}, 200),
        (200, {"code": 0, "data": {"fields": ["close"], "items": []}}, 200),
        (200, {"code": -1, "msg": "private-tushare-token"}, 503),
        (200, {"code": 2002, "msg": "no permission"}, 503),
        (401, {"code": 0}, 503),
        (503, {"code": 0}, 503),
        (302, {"code": 0}, 503),
        (200, {}, 503),
        (200, [], 503),
        (200, {"code": "0"}, 503),
        (200, {"code": False}, 503),
    ],
)
def test_tushare_check_uses_one_current_day_request(
    monkeypatch: pytest.MonkeyPatch,
    tushare_config: Mock,
    http_status: int,
    payload: object,
    expected_status: int,
) -> None:
    upstream = MagicMock(spec=requests.Response)
    upstream.__enter__.return_value = upstream
    upstream.status_code = http_status
    upstream.json.return_value = payload
    post = Mock(return_value=upstream)
    monkeypatch.setattr(requests, "post", post)
    runtime = _StubRuntime()
    client = create_app(cast(JobRuntime, runtime)).test_client()

    response = client.post("/checks/tushare")

    assert response.status_code == expected_status
    assert response.get_json() == (
        {"ok": True}
        if expected_status == 200
        else {
            "error": {
                "code": "tushare_check_failed",
                "message": "Tushare check failed",
            }
        }
    )
    post.assert_called_once_with(
        "https://private-gateway.example/dataapi/daily",
        json={
            "api_name": "daily",
            "token": "private-tushare-token",
            "params": {"trade_date": "20260920"},
            "fields": "",
        },
        timeout=30,
        allow_redirects=False,
    )
    upstream.__exit__.assert_called_once()
    tushare_config.assert_called_once_with()
    assert runtime.jobs == {}
    assert runtime.submitted == []


@pytest.mark.parametrize(
    ("failure_phase", "error"),
    [
        ("configuration", ValueError("private-tushare-token")),
        ("request", requests.Timeout("private-tushare-token")),
        ("request", requests.ConnectionError("private-tushare-token")),
        ("response", requests.JSONDecodeError("private-tushare-token", "", 0)),
    ],
)
def test_tushare_check_failure_is_private_and_does_not_affect_health(
    monkeypatch: pytest.MonkeyPatch,
    tushare_config: Mock,
    failure_phase: str,
    error: Exception,
) -> None:
    upstream = MagicMock(spec=requests.Response)
    upstream.__enter__.return_value = upstream
    upstream.status_code = 200
    post = Mock(return_value=upstream)
    monkeypatch.setattr(requests, "post", post)
    if failure_phase == "configuration":
        tushare_config.side_effect = error
    elif failure_phase == "request":
        post.side_effect = error
    else:
        upstream.json.side_effect = error
    client = create_app(cast(JobRuntime, _StubRuntime())).test_client()
    messages: list[str] = []
    sink_id = api_module.logs.add(messages.append, format="{message}")
    try:
        response = client.post("/checks/tushare")
        health = client.get("/health")
    finally:
        api_module.logs.remove(sink_id)

    assert response.status_code == 503
    assert response.get_json() == {
        "error": {
            "code": "tushare_check_failed",
            "message": "Tushare check failed",
        }
    }
    assert "private-tushare-token" not in "".join(messages)
    assert "private-gateway" not in "".join(messages)
    assert "Traceback" not in "".join(messages)
    assert health.status_code == 200
    assert health.get_json()["ok"] is True
    tushare_config.assert_called_once_with()
    assert post.call_count == (0 if failure_phase == "configuration" else 1)


def test_tushare_check_reloads_token_and_gateway(
    monkeypatch: pytest.MonkeyPatch,
    tushare_config: Mock,
) -> None:
    first_secret = tushare_config.return_value.secret.model_copy(
        update={"tushare_gateway": None}
    )
    second_secret = first_secret.model_copy(
        update={
            "tushare_token": "rotated-tushare-token",
            "tushare_gateway": "https://rotated-gateway.example",
        }
    )
    tushare_config.side_effect = [
        Mock(spec=AppConfig, secret=first_secret),
        Mock(spec=AppConfig, secret=second_secret),
    ]
    upstream = MagicMock(spec=requests.Response)
    upstream.__enter__.return_value = upstream
    upstream.status_code = 200
    upstream.json.return_value = {"code": 0}
    post = Mock(return_value=upstream)
    monkeypatch.setattr(requests, "post", post)
    client = create_app(cast(JobRuntime, _StubRuntime())).test_client()

    assert client.post("/checks/tushare").status_code == 200
    assert client.post("/checks/tushare").status_code == 200

    assert tushare_config.call_count == 2
    assert post.call_args_list == [
        call(
            f"{gateway}/daily",
            json={
                "api_name": "daily",
                "token": token,
                "params": {"trade_date": "20260920"},
                "fields": "",
            },
            timeout=30,
            allow_redirects=False,
        )
        for gateway, token in (
            ("http://api.waditu.com/dataapi", "private-tushare-token"),
            ("https://rotated-gateway.example", "rotated-tushare-token"),
        )
    ]


def test_data_range_returns_one_job_and_only_public_fields() -> None:
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
        {"start": "2026-07-20", "end": "2026-07-21"},
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
        {"kind": "data-calendar"},
        {"kind": "data-standard-bootstrap"},
        {"kind": "data-feature-backfill"},
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


def test_job_runtime_failure_does_not_change_health_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosedRuntime(_StubRuntime):
        def submit(
            self,
            submissions: Sequence[JobSubmission],
        ) -> list[JobSnapshot]:
            raise RuntimeError("job runtime is closed")

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("MINQUANT_RELEASE_REF", "release/auto-release")
    monkeypatch.setenv(
        "MINQUANT_COMMIT_SHA",
        "0123456789abcdef0123456789abcdef01234567",
    )
    app = create_app(cast(JobRuntime, ClosedRuntime()))
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("MINQUANT_RELEASE_REF", "master")
    monkeypatch.setenv("MINQUANT_COMMIT_SHA", "f" * 40)
    client = app.test_client()

    failed_submission = client.post(
        "/jobs",
        json={
            "kind": "data-standard",
            "start": "2026-07-20",
            "end": "2026-07-20",
        },
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
    assert health.get_json() == {
        "ok": True,
        "environment": "test",
        "release_ref": "release/auto-release",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
    }


@pytest.mark.parametrize(
    ("configured_host", "configured_port", "expected_host", "expected_port"),
    [
        (None, None, "0.0.0.0", 5051),
        ("127.0.0.1", "5050", "127.0.0.1", 5050),
    ],
)
def test_main_runs_flask_on_the_configured_address(
    monkeypatch: pytest.MonkeyPatch,
    configured_host: str | None,
    configured_port: str | None,
    expected_host: str,
    expected_port: int,
) -> None:
    if configured_host is None:
        monkeypatch.delenv("MINQUANT_API_HOST", raising=False)
    else:
        monkeypatch.setenv("MINQUANT_API_HOST", configured_host)
    if configured_port is None:
        monkeypatch.delenv("MINQUANT_API_PORT", raising=False)
    else:
        monkeypatch.setenv("MINQUANT_API_PORT", configured_port)

    job_runtime = cast(JobRuntime, object())
    captured_app = Mock()
    monkeypatch.setattr(
        api_module,
        "JobRuntime",
        lambda: nullcontext(job_runtime),
    )
    monkeypatch.setattr(api_module, "create_app", lambda _: captured_app)
    monkeypatch.setattr(api_module, "configure_system_logging", lambda _: None)
    monkeypatch.setattr(api_module.logs, "info", lambda _: None)
    monkeypatch.setattr(api_module.logs, "complete", lambda: None)
    monkeypatch.setattr(api_module.logs, "remove", lambda: None)

    api_module.main()

    captured_app.run.assert_called_once_with(
        host=expected_host,
        port=expected_port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


@pytest.mark.parametrize("configured_port", ["", "not-a-port", "0", "65536"])
def test_main_rejects_an_invalid_configured_port(
    monkeypatch: pytest.MonkeyPatch,
    configured_port: str,
) -> None:
    monkeypatch.setenv("MINQUANT_API_HOST", "0.0.0.0")
    monkeypatch.setenv("MINQUANT_API_PORT", configured_port)

    with pytest.raises(
        ValueError,
        match="MINQUANT_API_PORT must be an integer from 1 to 65535",
    ):
        api_module.main()


def test_main_rejects_a_blank_configured_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINQUANT_API_HOST", " ")
    monkeypatch.setenv("MINQUANT_API_PORT", "5051")

    with pytest.raises(ValueError, match="MINQUANT_API_HOST must be non-blank"):
        api_module.main()
