# filepath: tests/scripts/test_install_offline_data_cron.py
from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "install_offline_data_cron.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _make_installer_fixture(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    project_root = tmp_path / "deploy" / "app" / "current"
    runner = project_root / "scripts" / "run_offline_data_jobs.sh"
    runner.parent.mkdir(parents=True)
    _write_executable(runner, "#!/usr/bin/env bash\nexit 0\n")
    storage_root = tmp_path / "deploy" / "data"
    storage_root.mkdir(parents=True)

    existing_crontab = tmp_path / "existing-crontab"
    installed_crontab = tmp_path / "installed-crontab"
    fake_crontab = tmp_path / "crontab"
    _write_executable(
        fake_crontab,
        """#!/usr/bin/env bash
set -Eeuo pipefail
case "${1:-}" in
  -l)
    if [[ -f "$TEST_EXISTING_CRONTAB" ]]; then
      /bin/cat "$TEST_EXISTING_CRONTAB"
      exit 0
    fi
    exit 1
    ;;
  -)
    /bin/cat >"$TEST_INSTALLED_CRONTAB"
    ;;
  *)
    exit 2
    ;;
esac
""",
    )
    environment = {
        **os.environ,
        "MINQUANT_CRONTAB_BIN": str(fake_crontab),
        "MINQUANT_PROJECT_ROOT": str(project_root),
        "ZERO_STORAGE_ROOT": str(storage_root),
        "MINQUANT_OFFLINE_DATA_CRON_SCHEDULE": "17 23 * * MON-FRI",
        "MINQUANT_OFFLINE_DATA_CRON_PATH": "/usr/local/bin:/usr/bin:/bin",
        "TEST_EXISTING_CRONTAB": str(existing_crontab),
        "TEST_INSTALLED_CRONTAB": str(installed_crontab),
    }
    return environment, existing_crontab, installed_crontab


def test_installer_replaces_only_its_managed_crontab_block(tmp_path: Path) -> None:
    environment, existing_crontab, installed_crontab = _make_installer_fixture(
        tmp_path
    )
    existing_crontab.write_text(
        """MAILTO=operator@example.invalid
# BEGIN min_quant offline data jobs
0 0 * * * obsolete-command
# END min_quant offline data jobs
5 1 * * * backup-command
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(INSTALLER)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    installed = installed_crontab.read_text(encoding="utf-8")
    project_root = Path(environment["MINQUANT_PROJECT_ROOT"])
    storage_root = Path(environment["ZERO_STORAGE_ROOT"])
    runner = project_root / "scripts" / "run_offline_data_jobs.sh"
    log_file = project_root / "logs" / "cron" / "offline_data_jobs.log"
    expected_line = (
        f'17 23 * * MON-FRI cd "{project_root}" && '
        'PATH="/usr/local/bin:/usr/bin:/bin" '
        f'MINQUANT_PROJECT_ROOT="{project_root}" '
        f'ZERO_STORAGE_ROOT="{storage_root}" '
        f'/usr/bin/env bash "{runner}" >> "{log_file}" 2>&1'
    )
    assert installed.count("# BEGIN min_quant offline data jobs") == 1
    assert installed.count("# END min_quant offline data jobs") == 1
    assert "MAILTO=operator@example.invalid" in installed
    assert "5 1 * * * backup-command" in installed
    assert "obsolete-command" not in installed
    assert expected_line in installed
    assert "MINQUANT_OFFLINE_DATA_DATE" not in installed
    assert "/home/wsw" not in installed
    assert "/home/ubuntu" not in installed


def test_installer_requires_an_explicit_schedule(tmp_path: Path) -> None:
    environment, _, installed_crontab = _make_installer_fixture(tmp_path)
    environment.pop("MINQUANT_OFFLINE_DATA_CRON_SCHEDULE")

    completed = subprocess.run(
        ["bash", str(INSTALLER)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "MINQUANT_OFFLINE_DATA_CRON_SCHEDULE" in completed.stderr
    assert not installed_crontab.exists()


def test_installer_rejects_a_multiline_schedule(tmp_path: Path) -> None:
    environment, _, installed_crontab = _make_installer_fixture(tmp_path)
    environment["MINQUANT_OFFLINE_DATA_CRON_SCHEDULE"] = (
        "17 23 * * MON-FRI\n* * * * * injected-command"
    )

    completed = subprocess.run(
        ["bash", str(INSTALLER)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert not installed_crontab.exists()


def test_installer_does_not_rewrite_an_unbalanced_managed_block(
    tmp_path: Path,
) -> None:
    environment, existing_crontab, installed_crontab = _make_installer_fixture(
        tmp_path
    )
    existing_crontab.write_text(
        """5 1 * * * backup-command
# BEGIN min_quant offline data jobs
0 0 * * * obsolete-command
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(INSTALLER)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 65
    assert "unbalanced" in completed.stderr
    assert not installed_crontab.exists()
