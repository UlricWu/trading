# filepath: src/utils/logger.py
from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

from loguru import logger as logs

_CLI_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)
_SYSTEM_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"


class ProcessLogger(Protocol):
    """Accept an already formatted operational info message."""

    def info(self, message: str) -> None:
        """Record one informational message."""
        ...


def configure_cli_logging() -> None:
    """Route CLI logs to stderr with compact source locations.

    Example:
        configure_cli_logging()
    """
    logs.remove()
    logs.add(sys.stderr, level="INFO", format=_CLI_LOG_FORMAT)


def configure_system_logging(
    system_log_file: Path,
) -> None:
    """Route Flask service logs to the system file and stderr.

    Example:
        configure_system_logging(
            Path("logs/system/2026-07-22-09-15-32.123456.log")
        )
    """
    if not isinstance(system_log_file, Path):
        raise TypeError("system_log_file must be a pathlib.Path")

    system_log_file.parent.mkdir(parents=True, exist_ok=True)
    logs.remove()
    logs.add(
        str(system_log_file),
        level="INFO",
        format=_SYSTEM_LOG_FORMAT,
        mode="x",
        enqueue=True,
        backtrace=True,
        diagnose=False,
        encoding="utf-8",
    )
    logs.add(
        sys.stderr,
        level="INFO",
        format=_SYSTEM_LOG_FORMAT,
        backtrace=True,
        diagnose=False,
    )


__all__ = [
    "ProcessLogger",
    "configure_cli_logging",
    "configure_system_logging",
    "logs",
]
