#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
SESSION="${MINQUANT_API_SESSION:-minquant_api}"
API_PORT="${MINQUANT_API_PORT-5051}"
HEALTH_URL="${MINQUANT_API_HEALTH_URL:-http://127.0.0.1:${API_PORT}/health}"
EXPECTED_ENVIRONMENT="${MINQUANT_EXPECTED_ENVIRONMENT:-}"
EXPECTED_RELEASE_REF="${MINQUANT_EXPECTED_RELEASE_REF:-}"
EXPECTED_COMMIT_SHA="${MINQUANT_EXPECTED_COMMIT_SHA:-}"

if ! command -v tmux >/dev/null 2>&1; then
    echo "missing required command: tmux" >&2
    exit 127
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "project Python interpreter is missing or not executable: $PYTHON_BIN" >&2
    exit 127
fi

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "API tmux session is not running: $SESSION"
    exit 1
fi

echo "API tmux session is running: $SESSION"
if ! command -v curl >/dev/null 2>&1; then
    echo "health not checked: curl is unavailable" >&2
    exit 2
fi
if ! health_json="$(curl -fsS --max-time 2 "$HEALTH_URL")"; then
    echo "health check failed: $HEALTH_URL" >&2
    exit 1
fi

if ! printf '%s' "$health_json" | "$PYTHON_BIN" -c '
import json
import sys

payload = json.load(sys.stdin)
expected_environment, expected_release_ref, expected_commit_sha = sys.argv[1:]
if set(payload) != {"ok", "environment", "release_ref", "commit_sha"}:
    raise SystemExit(1)
if payload["ok"] is not True:
    raise SystemExit(1)
for field, expected in (
    ("environment", expected_environment),
    ("release_ref", expected_release_ref),
    ("commit_sha", expected_commit_sha),
):
    if expected and payload[field] != expected:
        raise SystemExit(1)
' "$EXPECTED_ENVIRONMENT" "$EXPECTED_RELEASE_REF" "$EXPECTED_COMMIT_SHA" >/dev/null; then
    echo "health identity mismatch: $HEALTH_URL" >&2
    exit 1
fi

echo "health check passed: $HEALTH_URL"
