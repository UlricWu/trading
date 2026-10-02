# filepath: tests/scripts/test_run_offline_data_jobs.py
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    (storage / "raw").mkdir(parents=True)
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
    _write_executable(commands / "mountpoint", "#!/usr/bin/env bash\nexit 0\n")
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


def test_runner_fails_before_health_check_when_raw_is_not_mounted(
    tmp_path: Path,
) -> None:
    commands = tmp_path / "commands"
    storage = tmp_path / "data"
    project_root = _deployment_root(tmp_path)
    commands.mkdir()
    (storage / "raw").mkdir(parents=True)
    curl_calls = tmp_path / "curl.calls"
    _write_executable(
        commands / "curl",
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {curl_calls}\n"
        "exit 1\n",
    )
    _write_executable(commands / "flock", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(commands / "mountpoint", "#!/usr/bin/env bash\nexit 1\n")
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

    assert completed.returncode == 66
    assert "raw data root is not a mountpoint" in completed.stderr
    assert not curl_calls.exists()


@pytest.mark.parametrize("override_date", [None, "2026-09-18"])
def test_runner_submits_previous_shanghai_calendar_day_or_explicit_date(
    tmp_path: Path,
    override_date: str | None,
) -> None:
    commands = tmp_path / "commands"
    storage = tmp_path / "data"
    project_root = _deployment_root(tmp_path)
    commands.mkdir()
    (storage / "raw").mkdir(parents=True)
    curl_calls = tmp_path / "curl.calls"
    expected_date = override_date or "2026-09-19"
    fixed_clock = tmp_path / "clock"
    fixed_clock.mkdir()
    (fixed_clock / "sitecustomize.py").write_text(
        "from src.utils.datetime_utils import DateTimeUtils\n"
        "DateTimeUtils.today = staticmethod(lambda: '2026-09-20')\n",
        encoding="utf-8",
    )
    (project_root / ".venv").unlink()
    python_bin = project_root / ".venv/bin/python"
    python_bin.parent.mkdir(parents=True)
    _write_executable(
        python_bin,
        "#!/usr/bin/env bash\n"
        f'export PYTHONPATH="{fixed_clock}:$PYTHONPATH"\n'
        f'exec "{sys.executable}" "$@"\n',
    )
    _write_executable(
        commands / "curl",
        f"#!{sys.executable}\n"
        "import json,sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        f"calls=Path({str(curl_calls)!r})\n"
        "with calls.open('a') as stream: stream.write(json.dumps(args)+'\\n')\n"
        f"expected_date={expected_date!r}\n"
        "if args[-1].endswith('/health'):\n"
        " import subprocess\n"
        f" sha=subprocess.check_output(['git','-C',{str(project_root)!r},'rev-parse','HEAD'],text=True).strip()\n"
        " print(json.dumps(dict(ok=True,environment='test',release_ref='release/auto-release',commit_sha=sha)))\n"
        "else:\n"
        " if '-d' in args:\n"
        "  payload=json.loads(args[args.index('-d')+1])\n"
        "  kind=payload['kind']\n"
        " else:\n"
        "  kind='data-standard' if args[-1].endswith('1') else 'data-level2'\n"
        " job_id='00000000-0000-4000-8000-00000000000'+('1' if kind=='data-standard' else '2')\n"
        " job=dict(job_id=job_id,kind=kind,scope=dict(start=expected_date,end=expected_date),status='SUCCESS',submitted_at=None,started_at=None,finished_at=None)\n"
        " print(json.dumps(dict(jobs=[job]) if '-d' in args else job))\n",
    )
    _write_executable(commands / "flock", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(commands / "mountpoint", "#!/usr/bin/env bash\nexit 0\n")
    environment = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "MINQUANT_PROJECT_ROOT": str(project_root),
        "ZERO_STORAGE_ROOT": str(storage),
        "MINQUANT_OFFLINE_DATA_LOCK_FILE": str(tmp_path / "runner.lock"),
    }
    environment.pop("MINQUANT_OFFLINE_DATA_DATE", None)
    if override_date is not None:
        environment["MINQUANT_OFFLINE_DATA_DATE"] = override_date

    completed = subprocess.run(
        ["bash", str(RUNNER)],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )

    calls = [json.loads(line) for line in curl_calls.read_text().splitlines()]
    submissions = [
        json.loads(args[args.index("-d") + 1]) for args in calls if "-d" in args
    ]
    assert submissions == [
        {"kind": kind, "start": expected_date, "end": expected_date}
        for kind in ("data-standard", "data-level2")
    ]
    assert completed.returncode == 0, completed.stderr
