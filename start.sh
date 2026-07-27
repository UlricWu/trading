#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONDA_ENV="${MINQUANT_CONDA_ENV:-dev}"
SESSION="${MINQUANT_API_SESSION:-minquant_api}"
API_URL="http://127.0.0.1:5050"

# -------- 2. Env --------
export PYTHONPATH=$(pwd)
export ZERO_STORAGE_ROOT="${HOME}/data"

require_command() {
    local command_name="$1"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "missing required command: $command_name" >&2
        exit 127
    fi
}

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

require_command conda
require_command tmux

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "API tmux session already exists: $SESSION" >&2
    exit 1
fi

if ! find_seven_zip >/dev/null; then
    echo "warning: no 7z-compatible CLI found; Level-2 normalization will fail" >&2
fi

printf -v launch_command \
    'cd %q && exec conda run -n %q python -m src.jobs.api' \
    "$REPO_ROOT" \
    "$CONDA_ENV"
tmux new-session -d -s "$SESSION" "$launch_command"

echo "API process launched in tmux session: $SESSION"
echo "Health endpoint: ${API_URL}/health"
echo "Operational logs: ${REPO_ROOT}/logs/system/"
echo "A launched process is not proof of readiness; verify the health endpoint."
