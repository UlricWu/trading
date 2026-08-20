# filepath: tests/scripts/test_run_offline_data_jobs.py
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "run_offline_data_jobs.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _deployment_root(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
    (project_root / "src").symlink_to(REPO_ROOT / "src", target_is_directory=True)
    subprocess.run(["git", "init", "-q", str(project_root)], check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "test deployment"],
        cwd=project_root,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )
    return project_root


def test_runner_fails_on_wrong_systemd_api_identity_without_submitting_jobs(
    tmp_path: Path,
) -> None:
    commands = tmp_path / "commands"
    storage = tmp_path / "data"
    project_root = _deployment_root(tmp_path)
    commands.mkdir()
    storage.mkdir()
    curl_calls = tmp_path / "curl.calls"
    _write_executable(
        commands / "curl",
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {curl_calls}\n"
        "printf '%s\\n' "
        "'{\"ok\":true,\"environment\":\"test\","
        "\"release_ref\":\"release/auto-release\",\"commit_sha\":\"wrong\"}'\n",
    )
    _write_executable(commands / "flock", "#!/usr/bin/env bash\nexit 0\n")
    environment = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "MINQUANT_PROJECT_ROOT": str(project_root),
        "ZERO_STORAGE_ROOT": str(storage),
        "MINQUANT_OFFLINE_DATA_LOCK_FILE": str(tmp_path / "runner.lock"),
        "MINQUANT_OFFLINE_DATA_DATE": "2026-08-20",
    }

    completed = subprocess.run(
        ["bash", str(RUNNER)],
        check=False,
        text=True,
        capture_output=True,
        env=environment,
    )

    assert completed.returncode == 1
    assert "wrong release identity" in completed.stderr
    calls = curl_calls.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1
    assert calls[0].endswith("http://127.0.0.1:5050/health")
