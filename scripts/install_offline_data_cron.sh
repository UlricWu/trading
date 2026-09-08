#!/usr/bin/env bash
set -Eeuo pipefail

CRONTAB_BIN="${MINQUANT_CRONTAB_BIN:-crontab}"
PROJECT_ROOT="${MINQUANT_PROJECT_ROOT:-}"
STORAGE_ROOT="${ZERO_STORAGE_ROOT:-}"
CRON_SCHEDULE="${MINQUANT_OFFLINE_DATA_CRON_SCHEDULE:-}"
CRON_PATH="${MINQUANT_OFFLINE_DATA_CRON_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
CRON_LOG_DIR="${MINQUANT_OFFLINE_DATA_CRON_LOG_DIR:-${PROJECT_ROOT}/logs/cron}"
CRON_LOG_FILE="${CRON_LOG_DIR}/offline_data_jobs.log"
RUNNER="${PROJECT_ROOT}/scripts/run_offline_data_jobs.sh"
BEGIN_MARKER="# BEGIN min_quant offline data jobs"
END_MARKER="# END min_quant offline data jobs"

if (( $# != 0 )); then
    echo "install_offline_data_cron.sh does not accept positional arguments" >&2
    exit 2
fi

for required_path in "$PROJECT_ROOT" "$STORAGE_ROOT" "$CRON_LOG_DIR"; do
    if [[ ! "$required_path" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
        echo "cron paths must be safe absolute paths: $required_path" >&2
        exit 2
    fi
done
if [[ ! "$CRON_PATH" =~ ^/[A-Za-z0-9._/-]+(:/[A-Za-z0-9._/-]+)*$ ]]; then
    echo "MINQUANT_OFFLINE_DATA_CRON_PATH must contain safe absolute directories" >&2
    exit 2
fi
if [[ -z "$CRON_SCHEDULE" ]] \
    || [[ "$CRON_SCHEDULE" == *$'\n'* ]] \
    || [[ "$CRON_SCHEDULE" == *$'\r'* ]] \
    || [[ ! "$CRON_SCHEDULE" =~ ^[A-Za-z0-9*/,-]+[[:blank:]]+[A-Za-z0-9*/,-]+[[:blank:]]+[A-Za-z0-9*/,-]+[[:blank:]]+[A-Za-z0-9*/,-]+[[:blank:]]+[A-Za-z0-9*/,-]+$ ]]; then
    echo "MINQUANT_OFFLINE_DATA_CRON_SCHEDULE must be five safe cron fields: $CRON_SCHEDULE" >&2
    exit 2
fi
if [[ ! -d "$PROJECT_ROOT" ]]; then
    echo "MINQUANT_PROJECT_ROOT is not a directory: $PROJECT_ROOT" >&2
    exit 66
fi
if [[ ! -d "$STORAGE_ROOT" ]]; then
    echo "ZERO_STORAGE_ROOT is not a directory: $STORAGE_ROOT" >&2
    exit 66
fi
if [[ ! -x "$RUNNER" ]]; then
    echo "offline data runner is missing or not executable: $RUNNER" >&2
    exit 66
fi

CRON_LINE="${CRON_SCHEDULE} cd \"${PROJECT_ROOT}\" && PATH=\"${CRON_PATH}\" MINQUANT_PROJECT_ROOT=\"${PROJECT_ROOT}\" ZERO_STORAGE_ROOT=\"${STORAGE_ROOT}\" /usr/bin/env bash \"${RUNNER}\" >> \"${CRON_LOG_FILE}\" 2>&1"

if ! command -v "$CRONTAB_BIN" >/dev/null 2>&1; then
    echo "missing required command: $CRONTAB_BIN" >&2
    exit 127
fi

mkdir -p "$CRON_LOG_DIR"

existing="$("$CRONTAB_BIN" -l 2>/dev/null || true)"
if ! filtered="$(
    printf '%s\n' "$existing" | awk \
        -v begin="$BEGIN_MARKER" \
        -v end="$END_MARKER" '
        $0 == begin {
            if (inside == 1) malformed = 1
            inside = 1
            next
        }
        $0 == end {
            if (inside != 1) malformed = 1
            inside = 0
            next
        }
        inside != 1 { print }
        END {
            if (inside == 1 || malformed == 1) exit 2
        }
    '
)"; then
    echo "existing crontab contains an unbalanced min_quant marker block" >&2
    exit 65
fi

{
    if [ -n "$filtered" ]; then
        printf '%s\n' "$filtered"
    fi
    printf '%s\n' "$BEGIN_MARKER"
    printf '%s\n' "$CRON_LINE"
    printf '%s\n' "$END_MARKER"
} | "$CRONTAB_BIN" -

echo "Installed min_quant offline data cron:"
echo "$CRON_LINE"
