# filepath: tests/scripts/test_start_script.py
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
START_SCRIPT = REPO_ROOT / "start.sh"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_start_injects_release_identity_into_the_tmux_process(
    tmp_path: Path,
) -> None:
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    start_script = release_dir / "start.sh"
    shutil.copy2(START_SCRIPT, start_script)
    python_bin = release_dir / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    _write_executable(python_bin, "#!/usr/bin/env bash\nexit 0\n")
    tmux_capture = tmp_path / "tmux-arguments"
    _write_executable(
        command_dir / "tmux",
        """#!/usr/bin/env bash
if [[ "$1" == "has-session" ]]; then
  exit 1
fi
printf '%s\n' "$@" >"$TMUX_CAPTURE"
""",
    )
    environment = {
        **os.environ,
        "PATH": f"{command_dir}:{os.environ['PATH']}",
        "TMUX_CAPTURE": str(tmux_capture),
        "ENV": "test",
        "MINQUANT_RELEASE_REF": "release/auto-release",
        "MINQUANT_COMMIT_SHA": COMMIT_SHA,
        "MINQUANT_API_SESSION": "test-api",
        "MINQUANT_API_HOST": "127.0.0.1",
        "MINQUANT_API_PORT": "5052",
        "ZERO_STORAGE_ROOT": str(tmp_path / "data"),
    }
    environment.pop("MINQUANT_API_HEALTH_URL", None)

    completed = subprocess.run(
        ["bash", str(start_script)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    tmux_arguments = tmux_capture.read_text(encoding="utf-8")
    assert "new-session" in tmux_arguments
    assert "test-api" in tmux_arguments
    assert "ENV=test" in tmux_arguments
    assert "MINQUANT_RELEASE_REF=release/auto-release" in tmux_arguments
    assert f"MINQUANT_COMMIT_SHA={COMMIT_SHA}" in tmux_arguments
    assert "MINQUANT_API_HOST=127.0.0.1" in tmux_arguments
    assert "MINQUANT_API_PORT=5052" in tmux_arguments
    assert f"PYTHONPATH={release_dir}" in tmux_arguments
    assert f"ZERO_STORAGE_ROOT={tmp_path / 'data'}" in tmux_arguments
    assert str(python_bin) in tmux_arguments
    assert "conda" not in tmux_arguments
    assert "Health endpoint: http://127.0.0.1:5052/health" in completed.stdout
