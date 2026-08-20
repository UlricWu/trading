# filepath: tests/scripts/test_deploy_release.py
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_release.sh"


def _run(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, cwd=cwd, check=True, text=True, capture_output=True)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    source = tmp_path / "app" / "code"
    _run(["git", "init", "--bare", str(origin)], tmp_path)
    _run(["git", "init", str(seed)], tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], seed)
    _run(["git", "config", "user.name", "Test"], seed)
    (seed / ".gitignore").write_text(".venv/\n.env.test\nlogs\n", encoding="utf-8")
    (seed / "pyproject.toml").write_text("[project]\nname='test'\nversion='0'\n", encoding="utf-8")
    (seed / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (seed / "version.txt").write_text("one\n", encoding="utf-8")
    _run(["git", "add", "."], seed)
    _run(["git", "commit", "-m", "initial"], seed)
    initial = _run(["git", "rev-parse", "HEAD"], seed).stdout.strip()
    (seed / "version.txt").write_text("two\n", encoding="utf-8")
    _run(["git", "commit", "-am", "target"], seed)
    target = _run(["git", "rev-parse", "HEAD"], seed).stdout.strip()
    _run(["git", "remote", "add", "origin", str(origin)], seed)
    _run(["git", "push", "origin", f"HEAD:refs/heads/release/auto-release"], seed)
    source.parent.mkdir(parents=True)
    _run(["git", "clone", str(origin), str(source)], tmp_path)
    _run(["git", "checkout", "--detach", initial], source)
    return source, initial, target


def _environment(tmp_path: Path, source: Path, target: str) -> tuple[dict[str, str], Path]:
    app_root = tmp_path / "app"
    shared = app_root / "shared"
    data = app_root / "data"
    deploy = app_root / "deploy"
    commands = tmp_path / "commands"
    for path in (shared / "logs", data, deploy, commands):
        path.mkdir(parents=True, exist_ok=True)
    (shared / ".env.test").write_text("TEST=1\n", encoding="utf-8")
    calls = tmp_path / "systemctl.calls"
    _write_executable(
        commands / "uv",
        f"""#!/usr/bin/env python3
import os
import pathlib
import sys

if sys.argv[1:3] == ["python", "find"]:
    print({str(Path(sys.executable))!r})
elif sys.argv[1] == "sync":
    target = pathlib.Path.cwd() / ".venv" / "bin"
    target.mkdir(parents=True, exist_ok=True)
    (target / "python").symlink_to({str(Path(sys.executable))!r})
elif sys.argv[1] != "lock":
    raise SystemExit(2)
""",
    )
    _write_executable(
        commands / "systemctl",
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {calls}\nexit 0\n",
    )
    _write_executable(
        commands / "curl",
        "#!/usr/bin/env bash\nprintf '%s\\n' "
        + repr(
            json.dumps(
                {
                    "ok": True,
                    "environment": "test",
                    "release_ref": "release/auto-release",
                    "commit_sha": target,
                },
                separators=(",", ":"),
            )
        )
        + "\n",
    )
    _write_executable(commands / "flock", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        commands / "date",
        "#!/usr/bin/env bash\nprintf '%s\\n' '2026-08-20T00:00:00+00:00'\n",
    )
    environment = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "RUN_ID": "delivery-1",
        "DEPLOY_SHA": target,
        "DEPLOY_APP_ROOT": str(app_root),
        "DEPLOY_SOURCE_REPO": str(source),
        "MINQUANT_UV_BIN": str(commands / "uv"),
        "MINQUANT_SYSTEMCTL_BIN": str(commands / "systemctl"),
        "MINQUANT_CURL_BIN": str(commands / "curl"),
        "DEPLOY_HEALTH_MAX_ATTEMPTS": "2",
        "DEPLOY_HEALTH_REQUIRED_SUCCESSES": "2",
        "DEPLOY_HEALTH_INTERVAL_SECONDS": "0",
    }
    return environment, calls


def test_deploys_only_the_exact_remote_tip_and_records_success(tmp_path: Path) -> None:
    source, _, target = _repository(tmp_path)
    environment, calls = _environment(tmp_path, source, target)

    completed = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT)],
        check=False,
        text=True,
        capture_output=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert _run(["git", "rev-parse", "HEAD"], source).stdout.strip() == target
    assert (source / "version.txt").read_text(encoding="utf-8") == "two\n"
    assert (source / ".env.test").is_symlink()
    assert (source / "logs").is_symlink()
    record = (tmp_path / "app" / "deploy" / "current-test-release").read_text()
    assert f"commit_sha={target}\n" in record
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "--user stop minquant-api.service",
        "--user start minquant-api.service",
    ]


def test_stale_delivery_fails_before_stopping_service(tmp_path: Path) -> None:
    source, initial, target = _repository(tmp_path)
    environment, calls = _environment(tmp_path, source, target)
    environment["DEPLOY_SHA"] = initial

    completed = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT)],
        check=False,
        text=True,
        capture_output=True,
        env=environment,
    )

    assert completed.returncode == 65
    assert "stale or out-of-order delivery" in completed.stderr
    assert not calls.exists()
    assert _run(["git", "rev-parse", "HEAD"], source).stdout.strip() == initial
