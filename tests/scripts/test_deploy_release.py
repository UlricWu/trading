# filepath: tests/scripts/test_deploy_release.py
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_TARGET = REPO_ROOT / "scripts" / "deploy_release.sh"


@dataclass(frozen=True, slots=True)
class _DeploymentFixture:
    source_repo: Path
    releases_dir: Path
    environments_dir: Path
    current_link: Path
    shared_env_file: Path
    shared_log_dir: Path
    deploy_dir: Path
    service_state: Path
    command_dir: Path
    uv_log: Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def _create_deployment_fixture(tmp_path: Path) -> tuple[_DeploymentFixture, str]:
    remote_repo = tmp_path / "origin.git"
    source_repo = tmp_path / "source"
    remote_repo.mkdir()
    source_repo.mkdir()
    _git(remote_repo, "init", "--bare")
    _git(source_repo, "init")
    _git(source_repo, "config", "user.name", "Deployment Test")
    _git(source_repo, "config", "user.email", "deployment@example.invalid")
    _git(source_repo, "remote", "add", "origin", str(remote_repo))

    _write_executable(
        source_repo / "start.sh",
        """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s' "$MINQUANT_COMMIT_SHA" >"$TEST_SERVICE_STATE"
""",
    )
    _write_executable(
        source_repo / "kill.sh",
        """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s' stopped >"$TEST_SERVICE_STATE"
""",
    )
    _write_executable(
        source_repo / "status.sh",
        """#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(cat "$TEST_SERVICE_STATE")" == "$MINQUANT_EXPECTED_COMMIT_SHA" ]]
[[ "${TEST_FAIL_SHA:-}" != "$MINQUANT_EXPECTED_COMMIT_SHA" ]]
""",
    )
    (source_repo / "pyproject.toml").write_text(
        "[project]\nname='deployment-fixture'\nversion='1.0.0'\n",
        encoding="utf-8",
    )
    (source_repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (source_repo / "application.txt").write_text("release-a\n", encoding="utf-8")
    _git(source_repo, "add", ".")
    _git(source_repo, "commit", "-m", "initial test release")
    first_sha = _git(source_repo, "rev-parse", "HEAD")
    _git(
        source_repo,
        "push",
        "origin",
        "HEAD:refs/heads/release/auto-release",
    )

    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    shared_env_file = shared_dir / ".env.test"
    shared_env_file.write_text("FTP_HOST=test.invalid\n", encoding="utf-8")
    shared_log_dir = shared_dir / "logs"
    shared_log_dir.mkdir()
    releases_dir = tmp_path / "releases"
    releases_dir.mkdir()
    environments_dir = tmp_path / "environments"
    environments_dir.mkdir()
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()

    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    _write_executable(command_dir / "flock", "#!/usr/bin/env bash\nexit 0\n")
    for command_name in ("curl", "tmux"):
        _write_executable(
            command_dir / command_name,
            "#!/usr/bin/env bash\nexit 0\n",
        )
    _write_executable(
        command_dir / "uv",
        """#!/usr/bin/env bash
set -Eeuo pipefail
{
  printf 'cwd=%s' "$PWD"
  printf ' %q' "$@"
  printf '\n'
} >>"$TEST_UV_LOG"
if [[ "${1:-}" == "python" && "${2:-}" == "find" ]]; then
  printf '%s\n' "$TEST_PYTHON_BIN"
  exit 0
fi
if [[ "${1:-}" == "lock" && "${2:-}" == "--check" ]]; then
  exit 0
fi
if [[ "${1:-}" == "sync" ]]; then
  if grep -q 'fail-sync' uv.lock; then
    exit 42
  fi
  if [[ " $* " == *" --check "* ]]; then
    [[ -x "$UV_PROJECT_ENVIRONMENT/bin/python" ]]
    exit
  fi
  mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
  ln -s "$TEST_PYTHON_BIN" "$UV_PROJECT_ENVIRONMENT/bin/python"
  exit 0
fi
exit 2
""",
    )
    _write_executable(
        command_dir / "mv",
        """#!/usr/bin/env bash
if [[ "${1:-}" == "-Tf" ]]; then
  rm -f "$3"
  exec /bin/mv -f "$2" "$3"
fi
exec /bin/mv "$@"
""",
    )

    fixture = _DeploymentFixture(
        source_repo=source_repo,
        releases_dir=releases_dir,
        environments_dir=environments_dir,
        current_link=tmp_path / "current",
        shared_env_file=shared_env_file,
        shared_log_dir=shared_log_dir,
        deploy_dir=deploy_dir,
        service_state=tmp_path / "service-state",
        command_dir=command_dir,
        uv_log=tmp_path / "uv.log",
    )
    return fixture, first_sha


def _commit_release(
    fixture: _DeploymentFixture,
    *,
    path: str,
    content: str,
    message: str,
) -> str:
    (fixture.source_repo / path).write_text(content, encoding="utf-8")
    _git(fixture.source_repo, "add", path)
    _git(fixture.source_repo, "commit", "-m", message)
    commit_sha = _git(fixture.source_repo, "rev-parse", "HEAD")
    _git(
        fixture.source_repo,
        "push",
        "origin",
        "HEAD:refs/heads/release/auto-release",
    )
    return commit_sha


def _deploy(
    fixture: _DeploymentFixture,
    *,
    commit_sha: str,
    run_id: str,
    failed_health_sha: str = "",
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PATH": f"{fixture.command_dir}:{os.environ['PATH']}",
        "RUN_ID": run_id,
        "DEPLOY_SHA": commit_sha,
        "DEPLOY_SOURCE_REPO": str(fixture.source_repo),
        "DEPLOY_RELEASES_DIR": str(fixture.releases_dir),
        "DEPLOY_ENVIRONMENTS_DIR": str(fixture.environments_dir),
        "DEPLOY_CURRENT_LINK": str(fixture.current_link),
        "DEPLOY_SHARED_ENV_FILE": str(fixture.shared_env_file),
        "DEPLOY_SHARED_LOG_DIR": str(fixture.shared_log_dir),
        "DEPLOY_LOG_DIR": str(fixture.deploy_dir),
        "DEPLOY_LOG": str(fixture.deploy_dir / "release.log"),
        "DEPLOY_LOCK_FILE": str(fixture.deploy_dir / "release.lock"),
        "DEPLOY_RECORD_FILE": str(fixture.deploy_dir / "current-release"),
        "DEPLOY_HEALTH_MAX_ATTEMPTS": "1",
        "DEPLOY_HEALTH_INTERVAL_SECONDS": "0",
        "DEPLOY_HEALTH_REQUIRED_SUCCESSES": "1",
        "TEST_SERVICE_STATE": str(fixture.service_state),
        "TEST_FAIL_SHA": failed_health_sha,
        "TEST_PYTHON_BIN": sys.executable,
        "TEST_UV_LOG": str(fixture.uv_log),
    }
    return subprocess.run(
        ["bash", str(DEPLOY_TARGET)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_deployment_activates_the_exact_remote_commit(tmp_path: Path) -> None:
    fixture, commit_sha = _create_deployment_fixture(tmp_path)

    completed = _deploy(
        fixture,
        commit_sha=commit_sha,
        run_id="delivery-success",
    )

    assert completed.returncode == 0
    assert fixture.current_link.resolve() == fixture.releases_dir / commit_sha
    assert fixture.service_state.read_text(encoding="utf-8") == commit_sha
    record = (fixture.deploy_dir / "current-release").read_text(encoding="utf-8")
    assert f"commit_sha={commit_sha}" in record
    assert "run_id=delivery-success" in record
    assert (fixture.releases_dir / commit_sha / ".env.test").resolve() == (
        fixture.shared_env_file
    )
    assert (fixture.releases_dir / commit_sha / "logs").resolve() == (
        fixture.shared_log_dir
    )
    runtime_dir = (fixture.releases_dir / commit_sha / ".venv").resolve()
    assert runtime_dir.parent == fixture.environments_dir
    assert (runtime_dir / "bin" / "python").exists()
    assert f"runtime_id={runtime_dir.name}" in record
    assert f"runtime_dir={runtime_dir}" in record
    uv_log = fixture.uv_log.read_text(encoding="utf-8")
    assert "lock --check --python" in uv_log
    assert "sync --locked --no-dev --no-install-project" in uv_log
    assert "--no-python-downloads" in uv_log


def test_first_failed_deployment_leaves_no_active_release(tmp_path: Path) -> None:
    fixture, commit_sha = _create_deployment_fixture(tmp_path)

    completed = _deploy(
        fixture,
        commit_sha=commit_sha,
        run_id="delivery-first-unhealthy",
        failed_health_sha=commit_sha,
    )

    assert completed.returncode == 1
    assert not fixture.current_link.exists()
    assert fixture.service_state.read_text(encoding="utf-8") == "stopped"
    assert not (fixture.deploy_dir / "current-release").exists()


def test_deployment_rejects_a_stale_delivery_without_stopping_service(
    tmp_path: Path,
) -> None:
    fixture, first_sha = _create_deployment_fixture(tmp_path)
    assert _deploy(
        fixture,
        commit_sha=first_sha,
        run_id="delivery-first",
    ).returncode == 0
    second_sha = _commit_release(
        fixture,
        path="application.txt",
        content="release-b\n",
        message="second test release",
    )

    completed = _deploy(
        fixture,
        commit_sha=first_sha,
        run_id="delivery-stale",
    )

    assert completed.returncode == 65
    assert second_sha != first_sha
    assert fixture.current_link.resolve() == fixture.releases_dir / first_sha
    assert fixture.service_state.read_text(encoding="utf-8") == first_sha


def test_failed_candidate_rolls_back_and_still_returns_failure(
    tmp_path: Path,
) -> None:
    fixture, first_sha = _create_deployment_fixture(tmp_path)
    assert _deploy(
        fixture,
        commit_sha=first_sha,
        run_id="delivery-first",
    ).returncode == 0
    second_sha = _commit_release(
        fixture,
        path="application.txt",
        content="release-b\n",
        message="unhealthy test release",
    )

    completed = _deploy(
        fixture,
        commit_sha=second_sha,
        run_id="delivery-unhealthy",
        failed_health_sha=second_sha,
    )

    assert completed.returncode == 1
    assert fixture.current_link.resolve() == fixture.releases_dir / first_sha
    assert fixture.service_state.read_text(encoding="utf-8") == first_sha
    record = (fixture.deploy_dir / "current-release").read_text(encoding="utf-8")
    assert f"commit_sha={first_sha}" in record
    release_log = (fixture.deploy_dir / "release.log").read_text(encoding="utf-8")
    assert f"rollback succeeded; previous_commit_sha={first_sha}" in release_log


def test_releases_with_the_same_lock_share_one_runtime_environment(
    tmp_path: Path,
) -> None:
    fixture, first_sha = _create_deployment_fixture(tmp_path)
    assert _deploy(
        fixture,
        commit_sha=first_sha,
        run_id="delivery-first",
    ).returncode == 0
    second_sha = _commit_release(
        fixture,
        path="application.txt",
        content="release-b\n",
        message="change application only",
    )

    completed = _deploy(
        fixture,
        commit_sha=second_sha,
        run_id="delivery-same-runtime",
    )

    assert completed.returncode == 0
    first_runtime = (fixture.releases_dir / first_sha / ".venv").resolve()
    second_runtime = (fixture.releases_dir / second_sha / ".venv").resolve()
    assert first_runtime == second_runtime
    assert len(list(fixture.environments_dir.iterdir())) == 1
    sync_calls = [
        line
        for line in fixture.uv_log.read_text(encoding="utf-8").splitlines()
        if " sync " in line
    ]
    assert len(sync_calls) == 2
    assert "--check" not in sync_calls[0]
    assert "--check" in sync_calls[1]


def test_changed_lock_creates_a_new_runtime_environment(tmp_path: Path) -> None:
    fixture, first_sha = _create_deployment_fixture(tmp_path)
    assert _deploy(
        fixture,
        commit_sha=first_sha,
        run_id="delivery-first",
    ).returncode == 0
    second_sha = _commit_release(
        fixture,
        path="uv.lock",
        content="version = 1\n# runtime-b\n",
        message="change dependency lock",
    )

    completed = _deploy(
        fixture,
        commit_sha=second_sha,
        run_id="delivery-new-runtime",
    )

    assert completed.returncode == 0
    first_runtime = (fixture.releases_dir / first_sha / ".venv").resolve()
    second_runtime = (fixture.releases_dir / second_sha / ".venv").resolve()
    assert first_runtime != second_runtime
    assert len(list(fixture.environments_dir.iterdir())) == 2
    assert fixture.service_state.read_text(encoding="utf-8") == second_sha


def test_runtime_build_failure_does_not_interrupt_the_current_release(
    tmp_path: Path,
) -> None:
    fixture, first_sha = _create_deployment_fixture(tmp_path)
    assert _deploy(
        fixture,
        commit_sha=first_sha,
        run_id="delivery-first",
    ).returncode == 0
    second_sha = _commit_release(
        fixture,
        path="uv.lock",
        content="version = 1\n# fail-sync\n",
        message="dependency sync failure",
    )

    completed = _deploy(
        fixture,
        commit_sha=second_sha,
        run_id="delivery-runtime-failure",
    )

    assert completed.returncode == 42
    assert fixture.current_link.resolve() == fixture.releases_dir / first_sha
    assert fixture.service_state.read_text(encoding="utf-8") == first_sha
    assert len(list(fixture.environments_dir.iterdir())) == 1
    assert not (fixture.releases_dir / second_sha / ".venv").exists()
