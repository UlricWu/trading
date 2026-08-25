#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
SESSION="${MINQUANT_API_SESSION:-minquant_api}"
API_HOST="${MINQUANT_API_HOST-0.0.0.0}"
API_PORT="${MINQUANT_API_PORT-5051}"
HEALTH_URL="${MINQUANT_API_HEALTH_URL:-http://127.0.0.1:${API_PORT}/health}"
DEPLOY_ENVIRONMENT="${ENV:-dev}"
RELEASE_REF="${MINQUANT_RELEASE_REF:-workspace}"
COMMIT_SHA="${MINQUANT_COMMIT_SHA:-}"

if [[ -z "$COMMIT_SHA" ]]; then
    COMMIT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo workspace)"
fi

export PYTHONPATH="$REPO_ROOT"
export ZERO_STORAGE_ROOT="${ZERO_STORAGE_ROOT:-${HOME}/app/data}"

find_seven_zip() {
    local command_name
    for command_name in 7zz 7za 7z; do
        if command -v "$command_name" >/dev/null 2>&1; then
            command -v "$command_name"
            return 0
        fi
    done
    return 1
}

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "project Python interpreter is missing or not executable: $PYTHON_BIN" >&2
    exit 127
fi
if ! command -v tmux >/dev/null 2>&1; then
    echo "missing required command: tmux" >&2
    exit 127
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "API tmux session already exists: $SESSION" >&2
    exit 1
fi

if ! find_seven_zip >/dev/null; then
    echo "warning: no 7z-compatible CLI found; Level-2 normalization will fail" >&2
fi

printf -v launch_command \
    'cd %q && exec env ENV=%q MINQUANT_RELEASE_REF=%q MINQUANT_COMMIT_SHA=%q MINQUANT_API_HOST=%q MINQUANT_API_PORT=%q PYTHONPATH=%q ZERO_STORAGE_ROOT=%q %q -m src.jobs.api' \
    "$REPO_ROOT" \
    "$DEPLOY_ENVIRONMENT" \
    "$RELEASE_REF" \
    "$COMMIT_SHA" \
    "$API_HOST" \
    "$API_PORT" \
    "$PYTHONPATH" \
    "$ZERO_STORAGE_ROOT" \
    "$PYTHON_BIN"
tmux new-session -d -s "$SESSION" "$launch_command"

echo "API process launched in tmux session: $SESSION"
echo "Health endpoint: $HEALTH_URL"
echo "Operational logs: ${REPO_ROOT}/logs/system/"
echo "A launched process is not proof of readiness; verify the health endpoint."
