#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-$SCRIPT_DIR}"
LOG="${DEPLOY_LOG:-${DEPLOY_DIR}/test-deploy.log}"
LOCK_FILE="${DEPLOY_LOCK_FILE:-${DEPLOY_DIR}/test-deploy.lock}"
SSH_KNOWN_HOSTS="${SSH_KNOWN_HOSTS:-}"
REMOTE_SCRIPT="${REMOTE_SCRIPT:-}"
LOCK_TIMEOUT_SECONDS="${DEPLOY_LOCK_TIMEOUT_SECONDS:-600}"

TRAINING_MACHINE="${TRAINING_MACHINE:-}"
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
if [[ ! "$TRAINING_MACHINE" =~ ^([A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+$ ]]; then
  echo "TRAINING_MACHINE must be an SSH user/host or host alias" >&2
  exit 64
fi
if [[ ! "$REMOTE_SCRIPT" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "REMOTE_SCRIPT must be a safe absolute path" >&2
  exit 64
fi
if [[ "$SSH_KNOWN_HOSTS" != /* \
  || "$SSH_KNOWN_HOSTS" == *$'\n'* \
  || "$SSH_KNOWN_HOSTS" == *$'\r'* ]]; then
  echo "SSH_KNOWN_HOSTS must be an absolute path" >&2
  exit 64
fi
if [[ ! "$LOCK_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "DEPLOY_LOCK_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 64
fi
if [[ ! -f "$SSH_KNOWN_HOSTS" ]]; then
  echo "Pinned SSH known_hosts file not found: $SSH_KNOWN_HOSTS" >&2
  exit 66
fi

mkdir -p "$DEPLOY_DIR"
exec >>"$LOG" 2>&1

log_exit() {
  local exit_code=$?
  if [[ $exit_code -eq 0 ]]; then
    echo "deployment relay completed; run_id=$RUN_ID commit_sha=$DEPLOY_SHA"
  else
    echo "deployment relay failed; run_id=$RUN_ID commit_sha=$DEPLOY_SHA exit_code=$exit_code"
  fi
  echo "=================================================="
}
trap log_exit EXIT

echo
echo "=================================================="
echo "deployment relay started; run_id=$RUN_ID commit_sha=$DEPLOY_SHA"
echo "relay_user=$(whoami) relay_host=$(hostname) target=$TRAINING_MACHINE"

for required_command in flock ssh; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "missing required command: $required_command" >&2
    exit 127
  fi
done

exec 9>"$LOCK_FILE"
if ! flock -w "$LOCK_TIMEOUT_SECONDS" 9; then
  echo "deployment relay lock timed out; lock_file=$LOCK_FILE" >&2
  exit 75
fi

SSH_OPTS=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o IdentitiesOnly=yes
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$SSH_KNOWN_HOSTS"
)

printf -v remote_command \
  'RUN_ID=%q DEPLOY_SHA=%q bash %q' \
  "$RUN_ID" \
  "$DEPLOY_SHA" \
  "$REMOTE_SCRIPT"

if ssh "${SSH_OPTS[@]}" "$TRAINING_MACHINE" "$remote_command"; then
  echo "remote deployment succeeded; run_id=$RUN_ID commit_sha=$DEPLOY_SHA"
else
  remote_exit_code=$?
  echo "remote deployment failed; run_id=$RUN_ID commit_sha=$DEPLOY_SHA exit_code=$remote_exit_code" >&2
  exit "$remote_exit_code"
fi
