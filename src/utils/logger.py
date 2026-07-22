# filepath: src/utils/logger.py
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger as logs

_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"


def configure_system_logging(
    system_log_file: Path,
) -> None:
    """Route Flask service logs to the system file and stderr."""
    if not isinstance(system_log_file, Path):
        raise TypeError("system_log_file must be a pathlib.Path")

    system_log_file.parent.mkdir(parents=True, exist_ok=True)
    logs.remove()
    logs.add(
        str(system_log_file),
        level="INFO",
        format=_LOG_FORMAT,
        mode="x",
        enqueue=True,
        backtrace=True,
        diagnose=False,
        encoding="utf-8",
    )
    logs.add(
        sys.stderr,
        level="INFO",
        format=_LOG_FORMAT,
        backtrace=True,
        diagnose=False,
    )


__all__ = ["configure_system_logging", "logs"]
