# filepath: tests/scripts/test_uninstall_offline_data_cron.py
from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
UNINSTALLER = REPO_ROOT / "scripts" / "uninstall_offline_data_cron.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _uninstaller_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    existing_crontab = tmp_path / "existing-crontab"
    installed_crontab = tmp_path / "installed-crontab"
    fake_crontab = tmp_path / "crontab"
    _write_executable(
        fake_crontab,
        """#!/usr/bin/env bash
set -Eeuo pipefail
case "${1:-}" in
  -l)
    case "$TEST_LIST_MODE" in
      existing)
        /bin/cat "$TEST_EXISTING_CRONTAB"
        ;;
      missing)
        printf 'no crontab for test-user\n' >&2
        exit 1
        ;;
      failure)
        printf 'permission denied\n' >&2
        exit 1
        ;;
      diagnostic)
        /bin/cat "$TEST_EXISTING_CRONTAB"
        printf 'unexpected warning\n' >&2
        ;;
    esac
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
        "TEST_EXISTING_CRONTAB": str(existing_crontab),
        "TEST_INSTALLED_CRONTAB": str(installed_crontab),
        "TEST_LIST_MODE": "existing",
    }
    return environment, existing_crontab, installed_crontab


def _run_uninstaller(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(UNINSTALLER)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_uninstaller_removes_only_managed_crontab_blocks(tmp_path: Path) -> None:
    environment, existing_crontab, installed_crontab = _uninstaller_environment(
        tmp_path
    )
    existing_crontab.write_text(
        """MAILTO=operator@example.invalid
# BEGIN min_quant offline data jobs
17 23 * * MON-FRI managed-command
# END min_quant offline data jobs
5 1 * * * backup-command
""",
        encoding="utf-8",
    )

    completed = _run_uninstaller(environment)

    assert completed.returncode == 0, completed.stderr
    installed = installed_crontab.read_text(encoding="utf-8")
    assert "MAILTO=operator@example.invalid" in installed
    assert "5 1 * * * backup-command" in installed
    assert "min_quant offline data jobs" not in installed
    assert "managed-command" not in installed


def test_uninstaller_is_idempotent_when_managed_block_is_absent(
    tmp_path: Path,
) -> None:
    environment, existing_crontab, installed_crontab = _uninstaller_environment(
        tmp_path
    )
    existing_crontab.write_text(
        "5 1 * * * backup-command\n",
        encoding="utf-8",
    )

    completed = _run_uninstaller(environment)

    assert completed.returncode == 0, completed.stderr
    assert "No min_quant offline data cron" in completed.stdout
    assert not installed_crontab.exists()


def test_uninstaller_is_idempotent_when_crontab_does_not_exist(
    tmp_path: Path,
) -> None:
    environment, _, installed_crontab = _uninstaller_environment(tmp_path)
    environment["TEST_LIST_MODE"] = "missing"

    completed = _run_uninstaller(environment)

    assert completed.returncode == 0, completed.stderr
    assert "No min_quant offline data cron" in completed.stdout
    assert not installed_crontab.exists()


def test_uninstaller_does_not_rewrite_an_unbalanced_managed_block(
    tmp_path: Path,
) -> None:
    environment, existing_crontab, installed_crontab = _uninstaller_environment(
        tmp_path
    )
    existing_crontab.write_text(
        """5 1 * * * backup-command
# BEGIN min_quant offline data jobs
17 23 * * MON-FRI managed-command
""",
        encoding="utf-8",
    )

    completed = _run_uninstaller(environment)

    assert completed.returncode == 65
    assert "unbalanced" in completed.stderr
    assert not installed_crontab.exists()


def test_uninstaller_does_not_hide_a_crontab_read_failure(tmp_path: Path) -> None:
    environment, _, installed_crontab = _uninstaller_environment(tmp_path)
    environment["TEST_LIST_MODE"] = "failure"

    completed = _run_uninstaller(environment)

    assert completed.returncode == 1
    assert "permission denied" in completed.stderr
    assert not installed_crontab.exists()


def test_uninstaller_refuses_successful_reads_with_diagnostics(
    tmp_path: Path,
) -> None:
    environment, existing_crontab, installed_crontab = _uninstaller_environment(
        tmp_path
    )
    existing_crontab.write_text(
        """# BEGIN min_quant offline data jobs
17 23 * * MON-FRI managed-command
# END min_quant offline data jobs
""",
        encoding="utf-8",
    )
    environment["TEST_LIST_MODE"] = "diagnostic"

    completed = _run_uninstaller(environment)

    assert completed.returncode == 1
    assert "diagnostic output" in completed.stderr
    assert not installed_crontab.exists()
