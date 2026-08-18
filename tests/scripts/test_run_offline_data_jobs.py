# filepath: tests/scripts/test_run_offline_data_jobs.py
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "run_offline_data_jobs.sh"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
STANDARD_JOB_ID = "00000000-0000-4000-8000-000000000001"
LEVEL2_JOB_ID = "00000000-0000-4000-8000-000000000002"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_curl_script() -> str:
    return """#!/usr/bin/env bash
set -Eeuo pipefail
payload=""
url=""
while (( $# > 0 )); do
  case "$1" in
    -d)
      payload="$2"
      shift 2
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done

standard_id="00000000-0000-4000-8000-000000000001"
level2_id="00000000-0000-4000-8000-000000000002"
if [[ "$url" == "$TEST_JOBS_URL" ]]; then
  printf 'POST %s\n' "$payload" >>"$TEST_HTTP_CAPTURE"
  if [[ "$payload" == *'"kind":"data-standard"'* ]]; then
    kind="data-standard"
    job_id="$standard_id"
  elif [[ "$payload" == *'"kind":"data-level2"'* ]]; then
    kind="data-level2"
    job_id="$level2_id"
  else
    exit 22
  fi
  job_date="$(
    "$MINQUANT_PROJECT_ROOT/.venv/bin/python" -c \
      'import json, sys; print(json.loads(sys.argv[1])["start"])' \
      "$payload"
  )"
  if [[ -n "${TEST_JOB_DATE:-}" ]]; then
    expected_payload=$(printf \
      '{"kind":"%s","start":"%s","end":"%s"}' \
      "$kind" "$TEST_JOB_DATE" "$TEST_JOB_DATE")
    [[ "$payload" == "$expected_payload" ]] || exit 22
  fi
  printf '%s' "$job_date" >"$TEST_JOB_DATE_STATE"
  printf \
    '{"jobs":[{"job_id":"%s","kind":"%s","scope":{"start":"%s","end":"%s"},"status":"PENDING","submitted_at":"2026-07-20T23:00:00.000000+08:00","started_at":null,"finished_at":null}]}' \
    "$job_id" "$kind" "$job_date" "$job_date"
  exit 0
fi

case "$url" in
  "$TEST_JOBS_URL/$standard_id")
    kind="data-standard"
    job_id="$standard_id"
    status="${TEST_STANDARD_STATUS:-SUCCESS}"
    ;;
  "$TEST_JOBS_URL/$level2_id")
    kind="data-level2"
    job_id="$level2_id"
    status="${TEST_LEVEL2_STATUS:-SUCCESS}"
    ;;
  *)
    exit 22
    ;;
esac
printf 'GET %s\n' "$job_id" >>"$TEST_HTTP_CAPTURE"
job_date="$(/bin/cat "$TEST_JOB_DATE_STATE")"
printf \
  '{"job_id":"%s","kind":"%s","scope":{"start":"%s","end":"%s"},"status":"%s","submitted_at":"2026-07-20T23:00:00.000000+08:00","started_at":"2026-07-20T23:00:01.000000+08:00","finished_at":"2026-07-20T23:00:02.000000+08:00"}' \
  "$job_id" "$kind" "$job_date" "$job_date" "$status"
"""


def _runner_environment(tmp_path: Path, *, job_date: str = "2026-07-20") -> dict[str, str]:
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    _write_executable(command_dir / "curl", _fake_curl_script())
    _write_executable(
        command_dir / "flock",
        "#!/usr/bin/env bash\nexit \"${TEST_FLOCK_EXIT:-0}\"\n",
    )
    _write_executable(
        command_dir / "git",
        """#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$*" == *"rev-parse HEAD"* ]]
printf '%s\n' "$TEST_COMMIT_SHA"
""",
    )
    _write_executable(command_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(command_dir / "tmux", "#!/usr/bin/env bash\nexit 1\n")

    project_root = tmp_path / "project"
    python_bin = project_root / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    _write_executable(
        python_bin,
        """#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$*" == *"DateTimeUtils.today()"* ]]; then
  printf '%s\n' "$TEST_TODAY"
  exit 0
fi
PYTHONPATH="$TEST_REAL_REPO_ROOT" exec "$TEST_REAL_PYTHON" "$@"
""",
    )

    status_script = tmp_path / "status.sh"
    _write_executable(status_script, "#!/usr/bin/env bash\nexit 0\n")
    start_script = tmp_path / "start.sh"
    _write_executable(start_script, "#!/usr/bin/env bash\nexit 99\n")
    storage_root = tmp_path / "data"
    storage_root.mkdir()

    jobs_url = "http://127.0.0.1:5050/jobs"
    environment = {
        **os.environ,
        "PATH": f"{command_dir}:{os.environ['PATH']}",
        "MINQUANT_PROJECT_ROOT": str(project_root),
        "ZERO_STORAGE_ROOT": str(storage_root),
        "MINQUANT_START_SCRIPT": str(start_script),
        "MINQUANT_STATUS_SCRIPT": str(status_script),
        "MINQUANT_API_JOBS_URL": jobs_url,
        "MINQUANT_OFFLINE_DATA_LOCK_FILE": str(tmp_path / "runner.lock"),
        "MINQUANT_CRON_POLL_SLEEP": "0",
        "MINQUANT_CRON_STARTUP_SLEEP": "0",
        "MINQUANT_CRON_STARTUP_TIMEOUT": "1",
        "MINQUANT_OFFLINE_DATA_DATE": job_date,
        "TEST_HTTP_CAPTURE": str(tmp_path / "http-capture"),
        "TEST_JOB_DATE_STATE": str(tmp_path / "job-date-state"),
        "TEST_JOBS_URL": jobs_url,
        "TEST_JOB_DATE": job_date,
        "TEST_COMMIT_SHA": COMMIT_SHA,
        "TEST_REAL_PYTHON": str(REPO_ROOT / ".venv" / "bin" / "python"),
        "TEST_REAL_REPO_ROOT": str(REPO_ROOT),
        "TEST_TODAY": "2026-08-18",
    }
    return environment


def _run_runner(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(RUNNER)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_runner_submits_project_job_payloads_in_serial_order(tmp_path: Path) -> None:
    environment = _runner_environment(tmp_path)

    completed = _run_runner(environment)

    assert completed.returncode == 0, completed.stderr
    capture = Path(environment["TEST_HTTP_CAPTURE"]).read_text(
        encoding="utf-8"
    )
    assert capture.splitlines() == [
        'POST {"kind":"data-standard","start":"2026-07-20","end":"2026-07-20"}',
        f"GET {STANDARD_JOB_ID}",
        'POST {"kind":"data-level2","start":"2026-07-20","end":"2026-07-20"}',
        f"GET {LEVEL2_JOB_ID}",
    ]


def test_runner_attempts_level2_after_standard_failure(tmp_path: Path) -> None:
    environment = _runner_environment(tmp_path)
    environment["TEST_STANDARD_STATUS"] = "FAILED"

    completed = _run_runner(environment)

    assert completed.returncode == 1
    capture = Path(environment["TEST_HTTP_CAPTURE"]).read_text(
        encoding="utf-8"
    )
    assert f"GET {STANDARD_JOB_ID}" in capture
    assert 'POST {"kind":"data-level2"' in capture
    assert f"GET {LEVEL2_JOB_ID}" in capture


def test_runner_starts_api_with_the_current_test_release_identity(
    tmp_path: Path,
) -> None:
    environment = _runner_environment(tmp_path)
    health_state = tmp_path / "health-state"
    start_capture = tmp_path / "start-capture"
    status_script = Path(environment["MINQUANT_STATUS_SCRIPT"])
    _write_executable(
        status_script,
        """#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$MINQUANT_EXPECTED_ENVIRONMENT" == "test" ]]
[[ "$MINQUANT_EXPECTED_RELEASE_REF" == "release/auto-release" ]]
[[ "$MINQUANT_EXPECTED_COMMIT_SHA" == "$TEST_EXPECTED_SHA" ]]
[[ -f "$TEST_HEALTH_STATE" ]]
""",
    )
    start_script = Path(environment["MINQUANT_START_SCRIPT"])
    _write_executable(
        start_script,
        """#!/usr/bin/env bash
set -Eeuo pipefail
printf 'ENV=%s\n' "$ENV" >"$TEST_START_CAPTURE"
printf 'MINQUANT_RELEASE_REF=%s\n' "$MINQUANT_RELEASE_REF" >>"$TEST_START_CAPTURE"
printf 'MINQUANT_COMMIT_SHA=%s\n' "$MINQUANT_COMMIT_SHA" >>"$TEST_START_CAPTURE"
printf 'ZERO_STORAGE_ROOT=%s\n' "$ZERO_STORAGE_ROOT" >>"$TEST_START_CAPTURE"
: >"$TEST_HEALTH_STATE"
""",
    )
    environment.update(
        {
            "TEST_EXPECTED_SHA": COMMIT_SHA,
            "TEST_HEALTH_STATE": str(health_state),
            "TEST_START_CAPTURE": str(start_capture),
        }
    )

    completed = _run_runner(environment)

    assert completed.returncode == 0, completed.stderr
    assert start_capture.read_text(encoding="utf-8").splitlines() == [
        "ENV=test",
        "MINQUANT_RELEASE_REF=release/auto-release",
        f"MINQUANT_COMMIT_SHA={COMMIT_SHA}",
        f"ZERO_STORAGE_ROOT={environment['ZERO_STORAGE_ROOT']}",
    ]


def test_runner_uses_the_current_shanghai_date_when_no_override_is_set(
    tmp_path: Path,
) -> None:
    environment = _runner_environment(tmp_path)
    environment.pop("MINQUANT_OFFLINE_DATA_DATE")
    environment["TEST_JOB_DATE"] = environment["TEST_TODAY"]

    completed = _run_runner(environment)

    assert completed.returncode == 0, completed.stderr
    first_line = Path(environment["TEST_HTTP_CAPTURE"]).read_text(
        encoding="utf-8"
    ).splitlines()[0]
    payload = json.loads(first_line.removeprefix("POST "))
    assert payload["start"] == payload["end"]
    assert payload["start"] == "2026-08-18"


def test_runner_rejects_an_invalid_manual_date_before_calling_api(
    tmp_path: Path,
) -> None:
    environment = _runner_environment(tmp_path, job_date="2026-7-20")

    completed = _run_runner(environment)

    assert completed.returncode == 2
    assert "canonical YYYY-MM-DD" in completed.stderr
    assert not Path(environment["TEST_HTTP_CAPTURE"]).exists()


def test_runner_reports_lock_contention_without_calling_api(tmp_path: Path) -> None:
    environment = _runner_environment(tmp_path)
    environment["TEST_FLOCK_EXIT"] = "1"

    completed = _run_runner(environment)

    assert completed.returncode == 75
    assert "another offline data runner is active" in completed.stderr
    assert not Path(environment["TEST_HTTP_CAPTURE"]).exists()
