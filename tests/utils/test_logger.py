# filepath: tests/utils/test_logger.py
from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from loguru import logger as loguru_logger

import src.utils.logger as logger_module
from src import logs
from src.utils.logger import configure_system_logging


@pytest.fixture(autouse=True)
def restore_project_logger() -> Iterator[None]:
    """Give every test isolated sinks and restore stderr afterward."""
    logs.remove()
    logs.add(sys.stderr)
    try:
        yield
    finally:
        logs.complete()
        logs.remove()
        logs.add(sys.stderr)


def test_logger_module_exports_current_public_contract() -> None:
    assert logger_module.__all__ == ['ProcessLogger', 'configure_system_logging', 'logs']
    assert logs is loguru_logger
    assert logger_module.logs is logs


def test_configure_system_logging_requires_path() -> None:
    invalid_path = cast(Path, "logs/system/service.log")

    with pytest.raises(TypeError, match="system_log_file must be a pathlib.Path"):
        configure_system_logging(invalid_path)


def test_configure_system_logging_routes_to_new_file_and_stderr(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    replaced_sink_messages: list[str] = []
    logs.add(replaced_sink_messages.append, format="{message}")
    system_log_file = tmp_path / "logs" / "system" / "2026-07-22-09-15-32.123456.log"

    configure_system_logging(system_log_file)
    logs.info("test run_id=service-1")
    logs.complete()

    assert list(system_log_file.parent.iterdir()) == [system_log_file]
    assert "test run_id=service-1" in system_log_file.read_text(
        encoding="utf-8"
    )
    assert "test run_id=service-1" in capsys.readouterr().err
    assert replaced_sink_messages == []


def test_configure_system_logging_rejects_existing_run_file(
    tmp_path: Path,
) -> None:
    system_log_file = tmp_path / "system" / "2026-07-22-09-15-32.123456.log"
    system_log_file.parent.mkdir(parents=True)
    system_log_file.write_text("previous service run\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        configure_system_logging(system_log_file)

    assert system_log_file.read_text(encoding="utf-8") == "previous service run\n"
