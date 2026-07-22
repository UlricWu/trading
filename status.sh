#!/usr/bin/env bash
set -Eeuo pipefail

SESSION="${MINQUANT_API_SESSION:-minquant_api}"
HEALTH_URL="${MINQUANT_API_HEALTH_URL:-http://127.0.0.1:5050/health}"

if ! command -v tmux >/dev/null 2>&1; then
    echo "missing required command: tmux" >&2
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
if ! curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null; then
    echo "health check failed: $HEALTH_URL" >&2
    exit 1
fi

echo "health check passed: $HEALTH_URL"
