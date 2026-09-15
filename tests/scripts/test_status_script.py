# filepath: tests/scripts/test_status_script.py
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_SCRIPT = REPO_ROOT / "status.sh"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _status_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    status_script = release_dir / "status.sh"
    shutil.copy2(STATUS_SCRIPT, status_script)
    python_bin = release_dir / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    _write_executable(
        python_bin,
        f"#!/usr/bin/env bash\nexec {sys.executable!s} \"$@\"\n",
    )
    _write_executable(command_dir / "tmux", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        command_dir / "curl",
        """#!/usr/bin/env bash
printf '%s\n' "$@" >"$CURL_CAPTURE"
printf '%s' "$HEALTH_RESPONSE"
""",
    )
    health_response = json.dumps(
        {
            "ok": True,
            "environment": "test",
            "release_ref": "release/auto-release",
            "commit_sha": COMMIT_SHA,
        }
    )
    environment = {
        **os.environ,
        "PATH": f"{command_dir}:{os.environ['PATH']}",
        "CURL_CAPTURE": str(tmp_path / "curl-arguments"),
        "HEALTH_RESPONSE": health_response,
        "MINQUANT_API_SESSION": "test-api",
        "MINQUANT_EXPECTED_ENVIRONMENT": "test",
        "MINQUANT_EXPECTED_RELEASE_REF": "release/auto-release",
        "MINQUANT_EXPECTED_COMMIT_SHA": COMMIT_SHA,
    }
    environment.pop("MINQUANT_API_PORT", None)
    environment.pop("MINQUANT_API_HEALTH_URL", None)
    return status_script, environment


def test_status_accepts_the_expected_release_identity(tmp_path: Path) -> None:
    status_script, environment = _status_environment(tmp_path)

    completed = subprocess.run(
        ["bash", str(status_script)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "health check passed" in completed.stdout
    assert "http://127.0.0.1:5051/health" in (
        tmp_path / "curl-arguments"
    ).read_text(encoding="utf-8")


def test_status_rejects_a_different_commit_identity(tmp_path: Path) -> None:
    status_script, environment = _status_environment(tmp_path)
    environment["MINQUANT_EXPECTED_COMMIT_SHA"] = "f" * 40

    completed = subprocess.run(
        ["bash", str(status_script)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "health identity mismatch" in completed.stderr
