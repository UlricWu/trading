#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ROOT="${DEPLOY_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
APP_ROOT="${DEPLOY_APP_ROOT:-${DEPLOY_ROOT}/app}"
SOURCE_REPO="${DEPLOY_SOURCE_REPO:-${APP_ROOT}/code}"
RELEASES_DIR="${DEPLOY_RELEASES_DIR:-${APP_ROOT}/releases}"
ENVIRONMENTS_DIR="${DEPLOY_ENVIRONMENTS_DIR:-${APP_ROOT}/environments}"
CURRENT_LINK="${DEPLOY_CURRENT_LINK:-${APP_ROOT}/current}"
SHARED_ENV_FILE="${DEPLOY_SHARED_ENV_FILE:-${APP_ROOT}/shared/.env.test}"
SHARED_LOG_DIR="${DEPLOY_SHARED_LOG_DIR:-${APP_ROOT}/shared/logs}"
DATA_ROOT="${ZERO_STORAGE_ROOT:-${DEPLOY_ROOT}/data}"
LOG_DIR="${DEPLOY_LOG_DIR:-${DEPLOY_ROOT}/deploy}"
LOG="${DEPLOY_LOG:-${LOG_DIR}/test-release.log}"
LOCK_FILE="${DEPLOY_LOCK_FILE:-${LOG_DIR}/test-release.lock}"
RECORD_FILE="${DEPLOY_RECORD_FILE:-${LOG_DIR}/current-test-release}"

TEST_BRANCH="release/auto-release"
TEST_ENVIRONMENT="test"
UV_COMMAND="${MINQUANT_UV_BIN:-uv}"
API_SESSION="${MINQUANT_API_SESSION:-minquant_api}"
HEALTH_URL="${MINQUANT_API_HEALTH_URL:-http://127.0.0.1:5050/health}"
LOCK_TIMEOUT_SECONDS="${DEPLOY_LOCK_TIMEOUT_SECONDS:-600}"
HEALTH_MAX_ATTEMPTS="${DEPLOY_HEALTH_MAX_ATTEMPTS:-30}"
HEALTH_INTERVAL_SECONDS="${DEPLOY_HEALTH_INTERVAL_SECONDS:-2}"
HEALTH_REQUIRED_SUCCESSES="${DEPLOY_HEALTH_REQUIRED_SUCCESSES:-2}"

RUN_ID="${RUN_ID:-}"
DEPLOY_SHA="${DEPLOY_SHA:-}"

if [[ ! "$RUN_ID" =~ ^[A-Za-z0-9._:-]{1,128}$ ]]; then
  echo "RUN_ID must contain 1-128 safe identifier characters" >&2
  exit 64
fi
if [[ ! "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "DEPLOY_SHA must be one full lowercase commit SHA" >&2
  exit 64
fi
for positive_integer in \
  "$LOCK_TIMEOUT_SECONDS" \
  "$HEALTH_MAX_ATTEMPTS" \
  "$HEALTH_REQUIRED_SUCCESSES"; do
  if [[ ! "$positive_integer" =~ ^[1-9][0-9]*$ ]]; then
    echo "deployment timeout and attempt values must be positive integers" >&2
    exit 64
  fi
done
if [[ ! "$HEALTH_INTERVAL_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "DEPLOY_HEALTH_INTERVAL_SECONDS must be a non-negative integer" >&2
  exit 64
fi
if (( HEALTH_REQUIRED_SUCCESSES > HEALTH_MAX_ATTEMPTS )); then
  echo "required health successes cannot exceed maximum attempts" >&2
  exit 64
fi
for configured_path in \
  "$DEPLOY_ROOT" \
  "$APP_ROOT" \
  "$SOURCE_REPO" \
  "$RELEASES_DIR" \
  "$ENVIRONMENTS_DIR" \
  "$CURRENT_LINK" \
  "$SHARED_ENV_FILE" \
  "$SHARED_LOG_DIR" \
  "$DATA_ROOT" \
  "$LOG_DIR"; do
  if [[ "$configured_path" != /* || "$configured_path" == *$'\n'* || "$configured_path" == *$'\r'* ]]; then
    echo "deployment paths must be absolute single-line paths: $configured_path" >&2
    exit 64
  fi
done

mkdir -p "$LOG_DIR" "$RELEASES_DIR" "$ENVIRONMENTS_DIR" "$SHARED_LOG_DIR"
exec >>"$LOG" 2>&1

current_switch_path="${CURRENT_LINK}.next.${RUN_ID}"
runtime_environment_tmp=""

log_exit() {
  local exit_code=$?
  rm -f "$current_switch_path"
  if [[ -n "$runtime_environment_tmp" ]]; then
    rm -rf -- "$runtime_environment_tmp" || true
  fi
  if [[ $exit_code -eq 0 ]]; then
    echo "test deployment completed; run_id=$RUN_ID commit_sha=$DEPLOY_SHA"
  else
    echo "test deployment failed; run_id=$RUN_ID commit_sha=$DEPLOY_SHA exit_code=$exit_code"
  fi
  echo "=================================================="
}
trap log_exit EXIT

echo
echo "=================================================="
echo "test deployment started; run_id=$RUN_ID commit_sha=$DEPLOY_SHA"
echo "deploy_user=$(whoami) deploy_host=$(hostname) source_repo=$SOURCE_REPO"

for required_command in curl date flock git ln mktemp mv realpath sleep tmux; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "missing required command: $required_command" >&2
    exit 127
  fi
done
if ! UV_BIN="$(command -v "$UV_COMMAND")" || [[ ! -x "$UV_BIN" ]]; then
  echo "uv executable is unavailable: $UV_COMMAND" >&2
  exit 127
fi

if [[ ! -f "$SHARED_ENV_FILE" ]]; then
  echo "test environment file not found: $SHARED_ENV_FILE" >&2
  exit 66
fi

python_executable="$("$UV_BIN" python find --no-python-downloads 3.13)"
if [[ ! -x "$python_executable" ]]; then
  echo "uv did not resolve an executable Python 3.13 interpreter: $python_executable" >&2
  exit 66
fi
python_identity="$("$python_executable" -c '
import sys
import sysconfig

if sys.version_info[:2] != (3, 13):
    raise SystemExit(1)
print("|".join((sys.version, sys.implementation.cache_tag, sysconfig.get_platform())))
')"
if ! git -C "$SOURCE_REPO" rev-parse --git-dir >/dev/null 2>&1; then
  echo "deployment source is not a Git repository: $SOURCE_REPO" >&2
  exit 66
fi

exec 9>"$LOCK_FILE"
if ! flock -w "$LOCK_TIMEOUT_SECONDS" 9; then
  echo "test deployment lock timed out; lock_file=$LOCK_FILE" >&2
  exit 75
fi

switch_current_release() {
  local release_dir="$1"
  if [[ -e "$CURRENT_LINK" && ! -L "$CURRENT_LINK" ]]; then
    echo "current release path exists but is not a symbolic link: $CURRENT_LINK" >&2
    return 1
  fi
  rm -f "$current_switch_path"
  ln -s "$release_dir" "$current_switch_path"
  mv -Tf "$current_switch_path" "$CURRENT_LINK"
}

inject_runtime_link() {
  local link_path="$1"
  local target_path="$2"
  if [[ -e "$link_path" && ! -L "$link_path" ]]; then
    echo "runtime injection path exists but is not a symbolic link: $link_path" >&2
    return 1
  fi
  if [[ -L "$link_path" ]] && [[ "$(realpath "$link_path")" == "$(realpath "$target_path")" ]]; then
    return 0
  fi
  rm -f "$link_path"
  ln -s "$target_path" "$link_path"
}

start_release() {
  local release_dir="$1"
  local commit_sha="$2"
  ENV="$TEST_ENVIRONMENT" \
  MINQUANT_RELEASE_REF="$TEST_BRANCH" \
  MINQUANT_COMMIT_SHA="$commit_sha" \
  MINQUANT_API_SESSION="$API_SESSION" \
  ZERO_STORAGE_ROOT="$DATA_ROOT" \
    "$release_dir/start.sh"
}

release_is_healthy() {
  local release_dir="$1"
  local commit_sha="$2"
  MINQUANT_API_SESSION="$API_SESSION" \
  MINQUANT_API_HEALTH_URL="$HEALTH_URL" \
  MINQUANT_EXPECTED_ENVIRONMENT="$TEST_ENVIRONMENT" \
  MINQUANT_EXPECTED_RELEASE_REF="$TEST_BRANCH" \
  MINQUANT_EXPECTED_COMMIT_SHA="$commit_sha" \
    "$release_dir/status.sh"
}

wait_for_release_health() {
  local release_dir="$1"
  local commit_sha="$2"
  local consecutive_successes=0
  local attempt
  for ((attempt = 1; attempt <= HEALTH_MAX_ATTEMPTS; attempt += 1)); do
    if release_is_healthy "$release_dir" "$commit_sha"; then
      consecutive_successes=$((consecutive_successes + 1))
      if (( consecutive_successes >= HEALTH_REQUIRED_SUCCESSES )); then
        return 0
      fi
    else
      consecutive_successes=0
    fi
    if (( attempt < HEALTH_MAX_ATTEMPTS )); then
      sleep "$HEALTH_INTERVAL_SECONDS"
    fi
  done
  return 1
}

record_current_release() {
  local release_dir="$1"
  local record_tmp="${RECORD_FILE}.${RUN_ID}.tmp"
  {
    echo "run_id=$RUN_ID"
    echo "environment=$TEST_ENVIRONMENT"
    echo "release_ref=$TEST_BRANCH"
    echo "commit_sha=$DEPLOY_SHA"
    echo "release_dir=$release_dir"
    echo "runtime_id=$runtime_id"
    echo "runtime_dir=$runtime_environment_dir"
    echo "deployed_at=$(date '+%Y-%m-%dT%H:%M:%S%z')"
  } >"$record_tmp"
  mv -f "$record_tmp" "$RECORD_FILE"
}

rollback_release() {
  local failed_release_dir="$1"
  local previous_release_dir="$2"
  local previous_commit_sha="$3"

  "$failed_release_dir/kill.sh" || true
  if [[ -z "$previous_release_dir" ]]; then
    if [[ -L "$CURRENT_LINK" ]] && [[ "$(realpath "$CURRENT_LINK")" == "$failed_release_dir" ]]; then
      rm -f "$CURRENT_LINK"
    fi
    echo "rollback unavailable because no previous managed release exists" >&2
    return 1
  fi

  echo "rollback started; previous_commit_sha=$previous_commit_sha"
  switch_current_release "$previous_release_dir"
  if ! start_release "$previous_release_dir" "$previous_commit_sha"; then
    echo "rollback start failed; previous_commit_sha=$previous_commit_sha" >&2
    return 1
  fi
  if ! wait_for_release_health "$previous_release_dir" "$previous_commit_sha"; then
    echo "rollback health check failed; previous_commit_sha=$previous_commit_sha" >&2
    return 1
  fi
  echo "rollback succeeded; previous_commit_sha=$previous_commit_sha"
}

git -C "$SOURCE_REPO" fetch \
  --no-tags \
  origin \
  "+refs/heads/${TEST_BRANCH}:refs/remotes/origin/${TEST_BRANCH}"

remote_sha="$(git -C "$SOURCE_REPO" rev-parse "refs/remotes/origin/${TEST_BRANCH}")"
if [[ "$remote_sha" != "$DEPLOY_SHA" ]]; then
  echo "stale deployment request; requested=$DEPLOY_SHA current_remote=$remote_sha" >&2
  exit 65
fi
git -C "$SOURCE_REPO" cat-file -e "${DEPLOY_SHA}^{commit}"

previous_release_dir=""
previous_commit_sha=""
if [[ -L "$CURRENT_LINK" ]]; then
  previous_release_dir="$(realpath "$CURRENT_LINK")"
  if [[ ! -d "$previous_release_dir" ]]; then
    echo "current release link is broken: $CURRENT_LINK" >&2
    exit 66
  fi
  previous_commit_sha="$(git -C "$previous_release_dir" rev-parse HEAD)"
elif [[ -e "$CURRENT_LINK" ]]; then
  echo "current release path exists but is not a symbolic link: $CURRENT_LINK" >&2
  exit 66
fi

release_dir="${RELEASES_DIR}/${DEPLOY_SHA}"
if [[ -e "$release_dir" ]]; then
  release_sha="$(git -C "$release_dir" rev-parse HEAD)"
  if [[ "$release_sha" != "$DEPLOY_SHA" ]]; then
    echo "existing release directory has an unexpected commit: $release_dir" >&2
    exit 65
  fi
  if [[ -n "$(git -C "$release_dir" status --porcelain --untracked-files=no)" ]]; then
    echo "existing release contains tracked modifications: $release_dir" >&2
    exit 65
  fi
else
  git -C "$SOURCE_REPO" worktree add --detach "$release_dir" "$DEPLOY_SHA"
fi

inject_runtime_link "$release_dir/.env.test" "$SHARED_ENV_FILE"
inject_runtime_link "$release_dir/logs" "$SHARED_LOG_DIR"

for project_file in pyproject.toml uv.lock; do
  if [[ ! -f "$release_dir/$project_file" ]]; then
    echo "release dependency file is missing: $release_dir/$project_file" >&2
    exit 66
  fi
done
for release_entrypoint in start.sh kill.sh status.sh; do
  if [[ ! -x "$release_dir/$release_entrypoint" ]]; then
    echo "release entrypoint is missing or not executable: $release_dir/$release_entrypoint" >&2
    exit 66
  fi
done

(
  cd "$release_dir"
  "$UV_BIN" lock --check --python "$python_executable" --no-python-downloads
)

runtime_id="$("$python_executable" -c '
import hashlib
import sys
from pathlib import Path

components = (
    b"minquant-runtime-v1",
    Path(sys.argv[1]).read_bytes(),
    sys.argv[2].encode(),
    b"uv-sync--locked--no-dev--no-install-project",
)
digest = hashlib.sha256()
for component in components:
    digest.update(len(component).to_bytes(8, "big"))
    digest.update(component)
print(digest.hexdigest())
' "$release_dir/uv.lock" "$python_identity")"
if [[ ! "$runtime_id" =~ ^[0-9a-f]{64}$ ]]; then
  echo "failed to derive a valid runtime identity" >&2
  exit 70
fi
runtime_environment_dir="${ENVIRONMENTS_DIR}/${runtime_id}"

if [[ -e "$runtime_environment_dir" ]]; then
  if [[ ! -d "$runtime_environment_dir" || -L "$runtime_environment_dir" ]]; then
    echo "runtime environment path is not a managed directory: $runtime_environment_dir" >&2
    exit 66
  fi
  (
    cd "$release_dir"
    UV_PROJECT_ENVIRONMENT="$runtime_environment_dir" \
      "$UV_BIN" sync \
        --check \
        --locked \
        --no-dev \
        --no-install-project \
        --python "$python_executable" \
        --no-python-downloads
  )
else
  runtime_environment_tmp="$(
    mktemp -d "${ENVIRONMENTS_DIR}/.${runtime_id}.${RUN_ID}.XXXXXX"
  )"
  (
    cd "$release_dir"
    UV_PROJECT_ENVIRONMENT="$runtime_environment_tmp" \
      "$UV_BIN" sync \
        --locked \
        --no-dev \
        --no-install-project \
        --python "$python_executable" \
        --no-python-downloads
  )
  if [[ ! -x "$runtime_environment_tmp/bin/python" ]]; then
    echo "uv did not create the runtime Python interpreter" >&2
    exit 70
  fi
  mv -Tf "$runtime_environment_tmp" "$runtime_environment_dir"
  runtime_environment_tmp=""
fi

runtime_python="${runtime_environment_dir}/bin/python"
if [[ ! -x "$runtime_python" ]]; then
  echo "runtime Python interpreter is missing: $runtime_python" >&2
  exit 66
fi
runtime_python_identity="$("$runtime_python" -c '
import sys
import sysconfig

print("|".join((sys.version, sys.implementation.cache_tag, sysconfig.get_platform())))
')"
if [[ "$runtime_python_identity" != "$python_identity" ]]; then
  echo "runtime Python identity does not match the selected interpreter" >&2
  exit 65
fi
inject_runtime_link "$release_dir/.venv" "$runtime_environment_dir"

if [[ -n "$previous_release_dir" ]] \
  && [[ "$previous_release_dir" == "$release_dir" ]] \
  && release_is_healthy "$release_dir" "$DEPLOY_SHA"; then
  record_current_release "$release_dir"
  echo "release already active and healthy; commit_sha=$DEPLOY_SHA"
  exit 0
fi

git -C "$SOURCE_REPO" fetch \
  --no-tags \
  origin \
  "+refs/heads/${TEST_BRANCH}:refs/remotes/origin/${TEST_BRANCH}"
latest_remote_sha="$(git -C "$SOURCE_REPO" rev-parse "refs/remotes/origin/${TEST_BRANCH}")"
if [[ "$latest_remote_sha" != "$DEPLOY_SHA" ]]; then
  echo "deployment became stale before service interruption; requested=$DEPLOY_SHA current_remote=$latest_remote_sha" >&2
  exit 65
fi

service_control_dir="${previous_release_dir:-$release_dir}"
if ! "$service_control_dir/kill.sh"; then
  echo "failed to stop the existing test service" >&2
  exit 1
fi

if ! switch_current_release "$release_dir"; then
  rollback_release "$release_dir" "$previous_release_dir" "$previous_commit_sha" || true
  exit 1
fi

if ! start_release "$release_dir" "$DEPLOY_SHA"; then
  echo "candidate service failed to start; commit_sha=$DEPLOY_SHA" >&2
  rollback_release "$release_dir" "$previous_release_dir" "$previous_commit_sha" || true
  exit 1
fi

if ! wait_for_release_health "$release_dir" "$DEPLOY_SHA"; then
  echo "candidate health check failed; commit_sha=$DEPLOY_SHA" >&2
  rollback_release "$release_dir" "$previous_release_dir" "$previous_commit_sha" || true
  exit 1
fi

record_current_release "$release_dir"
echo "candidate became healthy; commit_sha=$DEPLOY_SHA release_dir=$release_dir runtime_id=$runtime_id"
