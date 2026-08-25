# filepath: src/jobs/api.py
from __future__ import annotations

import os
import time
from pathlib import Path

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import BadRequest, HTTPException, UnsupportedMediaType

from src import logs
from src.jobs.requests import InvalidJobRequest, parse_job_request
from src.jobs.runtime import (
    JobNotCancellableError,
    JobNotFoundError,
    JobRuntime,
    JobSnapshot,
    JobStatus,
)
from src.utils.datetime_utils import DateTimeUtils
from src.utils.logger import configure_system_logging


HttpResponse = Response | tuple[Response, int]


def create_app(job_runtime: JobRuntime) -> Flask:
    """Create the four-endpoint HTTP adapter for one explicit job runtime.

    API inputs:

    - ``POST /jobs`` accepts exactly one JSON object:

      - Data range:
        ``{"kind": "data-standard", "start": "YYYY-MM-DD",
        "end": "YYYY-MM-DD"}``, or the same object with ``kind`` set to
        ``"data-level2"``.
      - Training:
        ``{"kind": "train", "start": "YYYY-MM-DD",
        "end": "YYYY-MM-DD"}``.
      - Backtest:
        ``{"kind": "backtest", "mode": MODE, "start": "YYYY-MM-DD",
        "end": "YYYY-MM-DD", "model_experiment": BASENAME,
        "strategy": STRATEGY}``.

      ``MODE`` is ``signal_eval``, ``tradable_alpha_eval``,
      ``execution_eval``, ``risk_eval``, or ``full_backtest``. ``STRATEGY``
      must match the configured threshold or top-k hysteresis strategy
      schema. Dates are canonical, ranges require ``start <= end``, and extra
      fields are rejected. Every request creates one full-range Job; a data
      single day uses the same value for ``start`` and ``end``.

    - ``GET /jobs/<job_id>`` accepts the Job ID as a path value.
    - ``POST /jobs/<job_id>/cancel`` accepts the Job ID as a path value.
    - ``GET /health`` accepts no input and reports the process release identity.

    Example:
        with JobRuntime(Path("logs/jobs")) as job_runtime:
            flask_app = create_app(job_runtime)
    """
    flask_app = Flask(__name__, static_folder=None)
    health_environment = os.environ.get("ENV") or "dev"
    health_release_ref = os.environ.get("MINQUANT_RELEASE_REF") or "workspace"
    health_commit_sha = os.environ.get("MINQUANT_COMMIT_SHA") or "workspace"

    @flask_app.before_request
    def log_request() -> None:
        g.request_started_at = time.monotonic()
        logs.info(f"▶️ request; method={request.method} path={request.path}")

    @flask_app.after_request
    def log_response(response: Response) -> Response:
        duration_seconds = time.monotonic() - g.request_started_at
        response_message = (
            f"response; method={request.method} path={request.path} "
            f"status={response.status_code} duration_s={duration_seconds:.6f}"
        )
        if response.status_code >= 500:
            logs.error(f"❌ {response_message}")
        elif response.status_code >= 400:
            logs.warning(f"⚠️ {response_message}")
        else:
            logs.info(f"✅ {response_message}")
        return response

    @flask_app.errorhandler(InvalidJobRequest)
    def handle_invalid_job_request(error: InvalidJobRequest) -> HttpResponse:
        return _error_response(
            code="invalid_job_request",
            message=str(error),
            status_code=400,
            field=error.field,
        )

    @flask_app.errorhandler(JobNotFoundError)
    def handle_job_not_found(error: JobNotFoundError) -> HttpResponse:
        return _error_response(
            code="job_not_found",
            message="job not found",
            status_code=404,
        )

    @flask_app.errorhandler(JobNotCancellableError)
    def handle_job_not_cancellable(
        error: JobNotCancellableError,
    ) -> HttpResponse:
        return _error_response(
            code="job_not_cancellable",
            message="job is not cancellable",
            status_code=409,
        )

    @flask_app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException) -> HttpResponse:
        return _error_response(
            code="http_error",
            message=error.description,
            status_code=error.code or 500,
        )

    @flask_app.errorhandler(Exception)
    def handle_internal_error(error: Exception) -> HttpResponse:
        logs.opt(exception=error).error(
            f"❌ request; reason=internal_error method={request.method} "
            f"path={request.path}"
        )
        return _error_response(
            code="internal_error",
            message="internal server error",
            status_code=500,
        )

    @flask_app.post("/jobs")
    def create_jobs() -> HttpResponse:
        try:
            payload: object = request.get_json()
        except (BadRequest, UnsupportedMediaType) as exc:
            raise InvalidJobRequest(
                "request body must be a valid JSON object"
            ) from exc

        submissions = parse_job_request(payload)
        jobs = job_runtime.submit(submissions)
        return jsonify({"jobs": [_job_to_json(job) for job in jobs]}), 201

    @flask_app.get("/jobs/<job_id>")
    def get_job(job_id: str) -> Response:
        return jsonify(_job_to_json(job_runtime.get(job_id)))

    @flask_app.post("/jobs/<job_id>/cancel")
    def cancel_job(job_id: str) -> HttpResponse:
        job = job_runtime.cancel(job_id)
        status_code = 202 if job.status is JobStatus.CANCELLING else 200
        return jsonify(_job_to_json(job)), status_code

    @flask_app.get("/health")
    def health() -> Response:
        return jsonify(
            {
                "ok": True,
                "environment": health_environment,
                "release_ref": health_release_ref,
                "commit_sha": health_commit_sha,
            }
        )

    return flask_app


def _job_to_json(job: JobSnapshot) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "scope": {
            "start": job.scope.start,
            "end": job.scope.end,
        },
        "status": job.status.value,
        "submitted_at": job.submitted_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def _error_response(
    *,
    code: str,
    message: str,
    status_code: int,
    field: str | None = None,
) -> tuple[Response, int]:
    error: dict[str, object] = {
        "code": code,
        "message": message,
    }
    if field is not None:
        error["field"] = field
    return jsonify({"error": error}), status_code


def main() -> None:
    """Run one configured single-process Flask service and own its shutdown.

    Example:
        os.environ["MINQUANT_API_HOST"] = "127.0.0.1"
        os.environ["MINQUANT_API_PORT"] = "5051"
        main()
    """
    api_host = os.environ.get("MINQUANT_API_HOST", "0.0.0.0")
    api_port_value = os.environ.get("MINQUANT_API_PORT", "5051")
    if not api_host.strip():
        raise ValueError("MINQUANT_API_HOST must be non-blank")
    try:
        api_port = int(api_port_value)
    except ValueError as exc:
        raise ValueError(
            "MINQUANT_API_PORT must be an integer from 1 to 65535"
        ) from exc
    if not 1 <= api_port <= 65535:
        raise ValueError("MINQUANT_API_PORT must be an integer from 1 to 65535")

    started_at = DateTimeUtils.now()
    system_log_file = Path("logs") / "system" / f"{started_at:%Y-%m-%d-%H-%M-%S.%f}.log"
    configure_system_logging(system_log_file)
    pid = os.getpid()
    logs.info(f"▶️ api; pid={pid}")

    try:
        with JobRuntime() as job_runtime:
            flask_app = create_app(job_runtime)
            flask_app.run(
                host=api_host,
                port=api_port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
    except Exception:
        logs.exception(f"❌ api; pid={pid}")
        raise
    finally:
        logs.info(f"✅ api shutdown; pid={pid}")
        logs.complete()
        logs.remove()


if __name__ == "__main__":
    main()
