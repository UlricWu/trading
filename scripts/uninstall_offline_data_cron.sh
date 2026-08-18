#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

CRONTAB_BIN="${MINQUANT_CRONTAB_BIN:-crontab}"
BEGIN_MARKER="# BEGIN min_quant offline data jobs"
END_MARKER="# END min_quant offline data jobs"

if (( $# != 0 )); then
    echo "uninstall_offline_data_cron.sh does not accept positional arguments" >&2
    exit 2
fi
for required_command in awk mktemp; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        echo "missing required command: $required_command" >&2
        exit 127
    fi
done
if ! command -v "$CRONTAB_BIN" >/dev/null 2>&1; then
    echo "missing required command: $CRONTAB_BIN" >&2
    exit 127
fi

error_file="$(mktemp)"
trap 'rm -f -- "$error_file"' EXIT

if existing="$(LC_ALL=C "$CRONTAB_BIN" -l 2>"$error_file")"; then
    if [[ -s "$error_file" ]]; then
        echo "crontab -l produced unexpected diagnostic output; refusing to rewrite" >&2
        exit 1
    fi
else
    list_status=$?
    list_error="$(<"$error_file")"
    if (( list_status == 1 )) && [[ "$list_error" == *"no crontab"* ]]; then
        echo "No min_quant offline data cron is installed."
        exit 0
    fi
    echo "failed to read current crontab: $list_error" >&2
    exit "$list_status"
fi

if filtered="$(
    printf '%s\n' "$existing" | awk \
        -v begin="$BEGIN_MARKER" \
        -v end="$END_MARKER" '
        $0 == begin {
            found = 1
            if (inside == 1) malformed = 1
            inside = 1
            next
        }
        $0 == end {
            found = 1
            if (inside != 1) malformed = 1
            inside = 0
            next
        }
        inside != 1 { print }
        END {
            if (inside == 1 || malformed == 1) exit 2
            if (found != 1) exit 3
        }
    '
)"; then
    :
else
    filter_status=$?
    if (( filter_status == 3 )); then
        echo "No min_quant offline data cron is installed."
        exit 0
    fi
    if (( filter_status == 2 )); then
        echo "existing crontab contains an unbalanced min_quant marker block" >&2
        exit 65
    fi
    echo "failed to filter current crontab" >&2
    exit "$filter_status"
fi

if ! printf '%s\n' "$filtered" | "$CRONTAB_BIN" -; then
    echo "failed to install the crontab without the min_quant block" >&2
    exit 1
fi

echo "Uninstalled min_quant offline data cron."
