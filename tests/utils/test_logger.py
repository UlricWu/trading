# filepath: tests/utils/test_logger.py
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

import src
import src.utils.logger as logger_module
from src import logs
from src.utils.logger import (
    JobLogContext,
    LoggingSettings,
    configure_api_logging,
    configure_job_logging,
    configure_system_logging,
)


class _FailOnceSinkRegistry:
    """Exercise session cleanup failures without Loguru private APIs."""

    def __init__(self) -> None:
        self.removal_attempts: list[int | None] = []
        self.fail_once_sink_ids: set[int] = set()

    def remove(self, handler_id: int | None = None) -> None:
        self.removal_attempts.append(handler_id)
        if handler_id in self.fail_once_sink_ids:
            self.fail_once_sink_ids.remove(handler_id)
            raise OSError("sink close failed")


def test_src_exports_the_project_loguru_logger() -> None:
    assert src.__all__ == ["logs"]
    assert logs is loguru_logger


def test_import_does_not_create_log_directories(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repository_root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from src import logs; "
                'value = 1; logs.info(f"public logger import works; value={value}")'
            ),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "public logger import works; value=1" in result.stderr
    assert not (tmp_path / "logs").exists()


def test_api_and_system_configuration_route_public_logs_without_cross_writes(
    tmp_path: Path,
) -> None:
    settings = LoggingSettings(log_root=tmp_path)

    with configure_api_logging(settings):
        request_id = "request-1"
        logs.info(f"api event; request_id={request_id}")

    with configure_system_logging(settings):
        check_id = "check-1"
        logs.info(f"system event; check_id={check_id}")

    api_text = (tmp_path / "api" / "api.current.log").read_text(encoding="utf-8")
    system_text = (tmp_path / "system.log").read_text(encoding="utf-8")
    assert "request-1" in api_text
    assert "check-1" not in api_text
    assert "check-1" in system_text
    assert "request-1" not in system_text


def test_job_configuration_writes_public_logs_only_to_validated_partition(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    settings = LoggingSettings(log_root=tmp_path)
    context = JobLogContext(
        job_id="job-20260715_01",
        run_date=date(2026, 7, 15),
    )

    with configure_job_logging(settings, context):
        logs.info(f"job event; job_id={context.job_id}")

    expected_log = tmp_path / "jobs" / "2026-07-15" / "job-20260715_01.log"
    assert expected_log.is_file()
    assert context.job_id in expected_log.read_text(encoding="utf-8")
    assert expected_log.is_relative_to(tmp_path / "jobs")
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "job_id",
    [
        "",
        "../escape",
        "/tmp/escape",
        "nested/job",
        r"nested\job",
        " leading-space",
        "job id",
        "a" * 129,
    ],
)
def test_job_context_rejects_unsafe_job_ids(job_id: str) -> None:
    with pytest.raises(ValueError, match="job_id"):
        JobLogContext(job_id=job_id, run_date=date(2026, 7, 15))


def test_logging_settings_require_absolute_root() -> None:
    with pytest.raises(ValueError, match="absolute path"):
        LoggingSettings(log_root=Path("relative/logs"))


def test_logging_session_close_is_idempotent_and_terminal(tmp_path: Path) -> None:
    session = configure_job_logging(
        LoggingSettings(log_root=tmp_path),
        JobLogContext(job_id="job-1", run_date=date(2026, 7, 15)),
    )

    session.close()
    session.close()

    with pytest.raises(RuntimeError, match="closed"):
        session.__enter__()


def test_logging_session_attempts_all_sink_cleanup_and_retries_failures() -> None:
    backend = _FailOnceSinkRegistry()
    backend.fail_once_sink_ids.add(0)
    session = logger_module._OwnedLoggingSession(backend, sink_ids=(0, 1))

    with pytest.raises(ExceptionGroup, match="sinks failed to close"):
        session.close()

    assert backend.removal_attempts == [0, 1]
    with pytest.raises(RuntimeError, match="closing"):
        session.__enter__()

    session.close()
    assert backend.removal_attempts == [0, 1, 0]


def test_logger_module_exposes_only_bootstrap_contracts() -> None:
    assert set(logger_module.__all__) == {
        "JobLogContext",
        "LogLevel",
        "LoggingSession",
        "LoggingSettings",
        "ProcessLogger",
        "configure_api_logging",
        "configure_job_logging",
        "configure_system_logging",
    }
    assert not hasattr(logger_module, "logs")
    assert not hasattr(logger_module, "system_logs")
    assert not hasattr(logger_module, "Logging")
