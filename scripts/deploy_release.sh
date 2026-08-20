#!/usr/bin/env bash
# filepath: scripts/deploy_release.sh
set -Eeuo pipefail

umask 027

APP_ROOT="${DEPLOY_APP_ROOT:-/home/wsw/app}"
SOURCE_REPO="${DEPLOY_SOURCE_REPO:-${APP_ROOT}/code}"
SHARED_ENV_FILE="${DEPLOY_SHARED_ENV_FILE:-${APP_ROOT}/shared/.env.test}"
SHARED_LOG_DIR="${DEPLOY_SHARED_LOG_DIR:-${APP_ROOT}/shared/logs}"
DATA_ROOT="${ZERO_STORAGE_ROOT:-${APP_ROOT}/data}"
DEPLOY_DIR="${DEPLOY_STATE_DIR:-${APP_ROOT}/deploy}"
LOCK_FILE="${DEPLOY_LOCK_FILE:-${DEPLOY_DIR}/test-release.lock}"
RECORD_FILE="${DEPLOY_RECORD_FILE:-${DEPLOY_DIR}/current-test-release}"
API_ENV_FILE="${DEPLOY_API_ENV_FILE:-${DEPLOY_DIR}/api-release.env}"

TEST_BRANCH="release/auto-release"
TEST_ENVIRONMENT="test"
UV_COMMAND="${MINQUANT_UV_BIN:-/usr/local/bin/uv}"
SYSTEMCTL_COMMAND="${MINQUANT_SYSTEMCTL_BIN:-systemctl}"
CURL_COMMAND="${MINQUANT_CURL_BIN:-curl}"
API_UNIT="${MINQUANT_API_UNIT:-minquant-api.service}"
HEALTH_URL="${MINQUANT_API_HEALTH_URL:-http://127.0.0.1:5050/health}"
LOCK_TIMEOUT_SECONDS="${DEPLOY_LOCK_TIMEOUT_SECONDS:-600}"
HEALTH_MAX_ATTEMPTS="${DEPLOY_HEALTH_MAX_ATTEMPTS:-30}"
HEALTH_INTERVAL_SECONDS="${DEPLOY_HEALTH_INTERVAL_SECONDS:-2}"
HEALTH_REQUIRED_SUCCESSES="${DEPLOY_HEALTH_REQUIRED_SUCCESSES:-2}"

RUN_ID="${RUN_ID:-}"
DEPLOY_SHA="${DEPLOY_SHA:-}"

log() {
  printf 'event=%s run_id=%s commit_sha=%s\n' "$1" "$RUN_ID" "$DEPLOY_SHA"
}

fail() {
  printf 'event=deployment_error run_id=%s commit_sha=%s message=%s\n' \
    "$RUN_ID" "$DEPLOY_SHA" "$1" >&2
  exit "${2:-1}"
}

atomic_write() {
  local destination="$1"
  local temporary="${destination}.${RUN_ID}.tmp"
  umask 027
  cat >"$temporary"
  mv -f "$temporary" "$destination"
}

health_is_exact() {
  local response
  if ! response="$($CURL_COMMAND -fsS --max-time 2 "$HEALTH_URL")"; then
    return 1
  fi
  printf '%s' "$response" | "$PYTHON_EXECUTABLE" -c '
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
' "$DEPLOY_SHA"
}

wait_for_health() {
  local consecutive=0
  local attempt
  for ((attempt = 1; attempt <= HEALTH_MAX_ATTEMPTS; attempt += 1)); do
    if health_is_exact; then
      consecutive=$((consecutive + 1))
      if (( consecutive >= HEALTH_REQUIRED_SUCCESSES )); then
        return 0
      fi
    else
      consecutive=0
    fi
    if (( attempt < HEALTH_MAX_ATTEMPTS )); then
      sleep "$HEALTH_INTERVAL_SECONDS"
    fi
  done
  return 1
}

if [[ ! "$RUN_ID" =~ ^[A-Za-z0-9._:-]{1,128}$ ]]; then
  fail "invalid RUN_ID" 64
fi
if [[ ! "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  fail "invalid DEPLOY_SHA" 64
fi
for number in "$LOCK_TIMEOUT_SECONDS" "$HEALTH_MAX_ATTEMPTS" "$HEALTH_REQUIRED_SUCCESSES"; do
  [[ "$number" =~ ^[1-9][0-9]*$ ]] || fail "timeouts and attempt counts must be positive integers" 64
done
[[ "$HEALTH_INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || fail "health interval must be non-negative" 64
(( HEALTH_REQUIRED_SUCCESSES <= HEALTH_MAX_ATTEMPTS )) || fail "required health successes exceed attempts" 64

for path in \
  "$APP_ROOT" "$SOURCE_REPO" "$SHARED_ENV_FILE" "$SHARED_LOG_DIR" "$DATA_ROOT" \
  "$DEPLOY_DIR" "$LOCK_FILE" "$RECORD_FILE" "$API_ENV_FILE"; do
  [[ "$path" == /* && "$path" != *$'\n'* && "$path" != *$'\r'* ]] \
    || fail "deployment paths must be absolute single-line paths" 64
done

for command in "$UV_COMMAND" "$SYSTEMCTL_COMMAND" "$CURL_COMMAND" flock git mv sleep; do
  command -v "$command" >/dev/null 2>&1 || fail "missing command: $command" 127
done
[[ -d "$SOURCE_REPO/.git" ]] || fail "source repository is missing: $SOURCE_REPO" 66
[[ -f "$SHARED_ENV_FILE" ]] || fail "shared test environment file is missing" 66
[[ -d "$DATA_ROOT" ]] || fail "data root is missing" 66
mkdir -p "$DEPLOY_DIR" "$SHARED_LOG_DIR"

exec 9>"$LOCK_FILE"
flock -w "$LOCK_TIMEOUT_SECONDS" 9 || fail "deployment lock timed out" 75
log deployment_started

PYTHON_EXECUTABLE="$($UV_COMMAND python find --no-python-downloads 3.13)"
[[ -x "$PYTHON_EXECUTABLE" ]] || fail "uv did not find Python 3.13" 66
"$PYTHON_EXECUTABLE" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 13))' \
  || fail "resolved interpreter is not Python 3.13" 65

git -C "$SOURCE_REPO" fetch \
  --no-tags \
  origin \
  "+refs/heads/${TEST_BRANCH}:refs/remotes/origin/${TEST_BRANCH}"
REMOTE_SHA="$(git -C "$SOURCE_REPO" rev-parse "refs/remotes/origin/${TEST_BRANCH}")"
[[ "$REMOTE_SHA" == "$DEPLOY_SHA" ]] \
  || fail "stale or out-of-order delivery; remote tip is $REMOTE_SHA" 65
git -C "$SOURCE_REPO" cat-file -e "${DEPLOY_SHA}^{commit}"

CURRENT_SHA="$(git -C "$SOURCE_REPO" rev-parse HEAD)"
TRACKED_CHANGES="$(git -C "$SOURCE_REPO" status --porcelain --untracked-files=no)"
if [[ "$CURRENT_SHA" == "$DEPLOY_SHA" && -z "$TRACKED_CHANGES" ]] \
  && [[ -f "$API_ENV_FILE" ]] \
  && grep -Fxq "MINQUANT_COMMIT_SHA=$DEPLOY_SHA" "$API_ENV_FILE" \
  && "$SYSTEMCTL_COMMAND" --user is-active --quiet "$API_UNIT" \
  && health_is_exact; then
  log deployment_already_healthy
  exit 0
fi

"$SYSTEMCTL_COMMAND" --user stop "$API_UNIT"
git -C "$SOURCE_REPO" checkout --detach --force "$DEPLOY_SHA"
git -C "$SOURCE_REPO" reset --hard "$DEPLOY_SHA"
git -C "$SOURCE_REPO" clean -fd
[[ -f "$SOURCE_REPO/pyproject.toml" && -f "$SOURCE_REPO/uv.lock" ]] \
  || fail "dependency contract files are missing" 66

ln -sfn "$SHARED_ENV_FILE" "$SOURCE_REPO/.env.test"
ln -sfn "$SHARED_LOG_DIR" "$SOURCE_REPO/logs"
(
  cd "$SOURCE_REPO"
  "$UV_COMMAND" lock --check --python "$PYTHON_EXECUTABLE" --no-python-downloads
  "$UV_COMMAND" sync \
    --locked \
    --no-dev \
    --no-install-project \
    --python "$PYTHON_EXECUTABLE" \
    --no-python-downloads
)
[[ -x "$SOURCE_REPO/.venv/bin/python" ]] || fail "uv did not create project environment" 70

git -C "$SOURCE_REPO" fetch \
  --no-tags \
  origin \
  "+refs/heads/${TEST_BRANCH}:refs/remotes/origin/${TEST_BRANCH}"
LATEST_REMOTE_SHA="$(git -C "$SOURCE_REPO" rev-parse "refs/remotes/origin/${TEST_BRANCH}")"
[[ "$LATEST_REMOTE_SHA" == "$DEPLOY_SHA" ]] \
  || fail "deployment became stale before service start; remote tip is $LATEST_REMOTE_SHA" 65

{
  printf 'MINQUANT_COMMIT_SHA=%s\n' "$DEPLOY_SHA"
} | atomic_write "$API_ENV_FILE"

"$SYSTEMCTL_COMMAND" --user start "$API_UNIT"
wait_for_health || fail "API identity health check failed"

{
  printf 'run_id=%s\n' "$RUN_ID"
  printf 'environment=%s\n' "$TEST_ENVIRONMENT"
  printf 'release_ref=%s\n' "$TEST_BRANCH"
  printf 'commit_sha=%s\n' "$DEPLOY_SHA"
  printf 'source_repo=%s\n' "$SOURCE_REPO"
  printf 'deployed_at=%s\n' "$(date --iso-8601=seconds)"
} | atomic_write "$RECORD_FILE"

log deployment_succeeded
