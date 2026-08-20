#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="${MINQUANT_PROJECT_ROOT:-$SCRIPT_REPO_ROOT}"

API_BASE_URL="${MINQUANT_API_BASE_URL:-http://127.0.0.1:5050}"
API_BASE_URL="${API_BASE_URL%/}"
HEALTH_URL="${MINQUANT_API_HEALTH_URL:-${API_BASE_URL}/health}"
JOBS_URL="${MINQUANT_API_JOBS_URL:-${API_BASE_URL}/jobs}"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
STORAGE_ROOT="${ZERO_STORAGE_ROOT:-}"
LOCK_FILE="${MINQUANT_OFFLINE_DATA_LOCK_FILE:-${REPO_ROOT}/logs/cron/offline_data_jobs.lock}"
POST_MAX_TIME="${MINQUANT_CRON_POST_MAX_TIME:-30}"
GET_MAX_TIME="${MINQUANT_CRON_GET_MAX_TIME:-10}"
POLL_SLEEP="${MINQUANT_CRON_POLL_SLEEP:-5}"
JOB_DATE="${MINQUANT_OFFLINE_DATA_DATE:-}"
ENVIRONMENT="test"
RELEASE_REF="release/auto-release"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*" >&2
}

require_command() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log "missing required command: $cmd"
        exit 127
    fi
}

require_positive_integer() {
    local name="$1"
    local value="$2"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        log "$name must be a positive integer: $value"
        exit 2
    fi
}

require_non_negative_integer() {
    local name="$1"
    local value="$2"
    if [[ ! "$value" =~ ^[0-9]+$ ]]; then
        log "$name must be a non-negative integer: $value"
        exit 2
    fi
}

validate_job_date() {
    if [[ -z "$JOB_DATE" ]]; then
        if ! JOB_DATE="$(
            PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -c \
                'from src.utils.datetime_utils import DateTimeUtils; print(DateTimeUtils.today())'
        )"; then
            log "failed to resolve the current Asia/Shanghai date"
            return 1
        fi
    else
        if ! JOB_DATE="$(
            PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -c '
import sys

from src.utils.datetime_utils import DateTimeUtils

try:
    print(DateTimeUtils.require_system_date(sys.argv[1], field_name="date"))
except (TypeError, ValueError):
    raise SystemExit(2) from None
' "$JOB_DATE"
        )"; then
            log "MINQUANT_OFFLINE_DATA_DATE must be a canonical YYYY-MM-DD date"
            return 2
        fi
    fi
}

health_ok() {
    local response
    if ! response="$(curl -fsS --max-time "$GET_MAX_TIME" "$HEALTH_URL")"; then
        return 1
    fi
    printf '%s' "$response" | "$PYTHON_BIN" -c '
import json
import sys

payload = json.load(sys.stdin)
expected = {
    "ok": True,
    "environment": "test",
    "release_ref": "release/auto-release",
    "commit_sha": sys.argv[1],
}
raise SystemExit(0 if payload == expected else 1)
' "$COMMIT_SHA"
}

submit_data_job() {
    local kind="$1"
    local payload
    local response
    local job_id

    printf -v payload \
        '{"kind":"%s","start":"%s","end":"%s"}' \
        "$kind" \
        "$JOB_DATE" \
        "$JOB_DATE"
    log "submitting data job; kind=$kind start=$JOB_DATE end=$JOB_DATE"
    if ! response="$(
        curl -fsS \
            --max-time "$POST_MAX_TIME" \
            -H "Content-Type: application/json" \
            -d "$payload" \
            "$JOBS_URL"
    )"; then
        log "data job submission failed; kind=$kind"
        return 1
    fi

    if ! job_id="$(
        printf '%s' "$response" | "$PYTHON_BIN" -c '
import json
import sys
import uuid

payload = json.load(sys.stdin)
expected_kind, expected_date = sys.argv[1:]
if set(payload) != {"jobs"} or not isinstance(payload["jobs"], list):
    raise SystemExit(1)
if len(payload["jobs"]) != 1 or not isinstance(payload["jobs"][0], dict):
    raise SystemExit(1)
job = payload["jobs"][0]
if set(job) != {
    "job_id",
    "kind",
    "scope",
    "status",
    "submitted_at",
    "started_at",
    "finished_at",
}:
    raise SystemExit(1)
if job.get("kind") != expected_kind:
    raise SystemExit(1)
if job.get("scope") != {"start": expected_date, "end": expected_date}:
    raise SystemExit(1)
job_id = job.get("job_id")
if not isinstance(job_id, str) or str(uuid.UUID(job_id)) != job_id:
    raise SystemExit(1)
print(job_id)
' "$kind" "$JOB_DATE"
    )"; then
        log "data job submission returned an invalid response; kind=$kind"
        return 1
    fi
    log "data job accepted; kind=$kind job_id=$job_id"
    printf '%s\n' "$job_id"
}

wait_for_data_job() {
    local kind="$1"
    local job_id="$2"
    local response
    local status
    local previous_status=""

    while true; do
        if ! response="$(
            curl -fsS \
                --max-time "$GET_MAX_TIME" \
                "${JOBS_URL}/${job_id}"
        )"; then
            log "data job status request failed; kind=$kind job_id=$job_id"
            return 1
        fi
        if ! status="$(
            printf '%s' "$response" | "$PYTHON_BIN" -c '
import json
import sys

job = json.load(sys.stdin)
expected_job_id, expected_kind, expected_date = sys.argv[1:]
if not isinstance(job, dict):
    raise SystemExit(1)
if set(job) != {
    "job_id",
    "kind",
    "scope",
    "status",
    "submitted_at",
    "started_at",
    "finished_at",
}:
    raise SystemExit(1)
if job.get("job_id") != expected_job_id or job.get("kind") != expected_kind:
    raise SystemExit(1)
if job.get("scope") != {"start": expected_date, "end": expected_date}:
    raise SystemExit(1)
status = job.get("status")
if status not in {
    "PENDING",
    "RUNNING",
    "CANCELLING",
    "SUCCESS",
    "SKIPPED",
    "FAILED",
    "CANCELLED",
}:
    raise SystemExit(1)
print(status)
' "$job_id" "$kind" "$JOB_DATE"
        )"; then
            log "data job status response is invalid; kind=$kind job_id=$job_id"
            return 1
        fi

        if [[ "$status" != "$previous_status" ]]; then
            log "data job status changed; kind=$kind job_id=$job_id status=$status"
            previous_status="$status"
        fi
        case "$status" in
            SUCCESS)
                return 0
                ;;
            SKIPPED | FAILED | CANCELLED)
                return 1
                ;;
            PENDING | RUNNING | CANCELLING)
                sleep "$POLL_SLEEP"
                ;;
        esac
    done
}

run_data_job() {
    local kind="$1"
    local job_id

    if ! job_id="$(submit_data_job "$kind")"; then
        return 1
    fi
    wait_for_data_job "$kind" "$job_id"
}

main() {
    local failed=0

    if (( $# != 0 )); then
        log "run_offline_data_jobs.sh does not accept positional arguments"
        return 2
    fi
    for required_command in curl flock git sleep; do
        require_command "$required_command"
    done
    require_positive_integer MINQUANT_CRON_POST_MAX_TIME "$POST_MAX_TIME"
    require_positive_integer MINQUANT_CRON_GET_MAX_TIME "$GET_MAX_TIME"
    require_non_negative_integer MINQUANT_CRON_POLL_SLEEP "$POLL_SLEEP"
    if [[ ! -x "$PYTHON_BIN" ]]; then
        log "project Python interpreter is missing or not executable: $PYTHON_BIN"
        return 127
    fi
    if [[ "$STORAGE_ROOT" != /* || ! -d "$STORAGE_ROOT" ]]; then
        log "ZERO_STORAGE_ROOT must be an existing absolute directory: $STORAGE_ROOT"
        return 66
    fi
    if [[ "$LOCK_FILE" != /* || "$LOCK_FILE" == *$'\n'* || "$LOCK_FILE" == *$'\r'* ]]; then
        log "MINQUANT_OFFLINE_DATA_LOCK_FILE must be an absolute single-line path"
        return 2
    fi
    mkdir -p "${LOCK_FILE%/*}"
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log "another offline data runner is active; lock_file=$LOCK_FILE"
        return 75
    fi

    COMMIT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
    if [[ ! "$COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
        log "project root does not resolve to a full commit SHA: $REPO_ROOT"
        return 66
    fi
    validate_job_date
    if ! health_ok; then
        log "systemd-managed API is unavailable or has the wrong release identity: $HEALTH_URL"
        return 1
    fi
    log "systemd-managed API health check OK: $HEALTH_URL"
    if ! run_data_job "data-standard"; then
        failed=1
    fi
    if ! run_data_job "data-level2"; then
        failed=1
    fi
    return "$failed"
}

main "$@"
