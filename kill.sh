#!/usr/bin/env bash
set -Eeuo pipefail

SESSION="${MINQUANT_API_SESSION:-minquant_api}"

if ! command -v tmux >/dev/null 2>&1; then
    echo "missing required command: tmux" >&2
    exit 127
fi

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "API tmux session is not running: $SESSION"
    exit 0
fi

tmux kill-session -t "$SESSION"
echo "API tmux session stopped: $SESSION"
