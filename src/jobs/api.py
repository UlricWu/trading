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
    - ``GET /health`` accepts no input.

    Example:
        with JobRuntime(Path("logs/jobs")) as job_runtime:
            flask_app = create_app(job_runtime)
    """
    flask_app = Flask(__name__, static_folder=None)

    @flask_app.before_request
    def log_request() -> None:
        g.request_started_at = time.monotonic()
        logs.info(f"[HTTP] request method={request.method} path={request.path}")

    @flask_app.after_request
    def log_response(response: Response) -> Response:
        duration_seconds = time.monotonic() - g.request_started_at
        logs.info(
            f"[HTTP] response method={request.method} path={request.path} "
            f"status={response.status_code} duration_s={duration_seconds:.6f}"
        )
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
            f"[HTTP] internal_error method={request.method} path={request.path}"
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
        return jsonify({"ok": True})

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
    """Run one single-process Flask service and own its runtime shutdown.

    Example:
        main()
    """
    started_at = DateTimeUtils.now()
    system_log_file = Path("logs") / "system" / f"{started_at:%Y-%m-%d-%H-%M-%S.%f}.log"
    configure_system_logging(system_log_file)
    pid = os.getpid()
    logs.info(f"[SYSTEM] api.start pid={pid} started_at={started_at.isoformat()}")

    try:
        with JobRuntime() as job_runtime:
            flask_app = create_app(job_runtime)
            flask_app.run(
                host="0.0.0.0",
                port=5050,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
    except Exception:
        logs.exception(f"[SYSTEM] api.failed pid={pid}")
        raise
    finally:
        logs.info(f"[SYSTEM] api.stop pid={pid}")
        logs.complete()
        logs.remove()


if __name__ == "__main__":
    main()
