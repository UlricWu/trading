# filepath: src/utils/logger.py
"""项目统一 Loguru 入口和进程级 sink 配置。

普通业务模块只需要导入 ``src.logs``，不创建配置对象，也不管理 sink：

.. code-block:: python

    from src import logs

    logs.debug("[FS] directory created; path={}", directory)
    logs.info(
        "[HTTP] method={} path={}",
        request.method,
        request.path,
    )

日志调用使用 Loguru ``{}`` 占位符进行延迟格式化。``LoggingSettings``、
``JobLogContext`` 和 ``configure_*_logging`` 只供 API、system 或 job 进程的
composition root 统一调用；普通业务模块不得自行配置或关闭 sinks。
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Never, Protocol, Self, runtime_checkable

from loguru import logger as _loguru_logger


_JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"


class LogLevel(StrEnum):
    """Supported Loguru levels for process logs."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    """Filesystem and retention settings for the process-wide logger."""

    log_root: Path
    level: LogLevel = LogLevel.INFO
    retention_days: int = 30

    def __post_init__(self) -> None:
        if not isinstance(self.log_root, Path):
            raise TypeError("field 'log_root' must be a pathlib.Path")
        if not self.log_root.is_absolute():
            raise ValueError("field 'log_root' must be an absolute path")
        if not isinstance(self.level, LogLevel):
            raise TypeError("field 'level' must be a LogLevel")
        if type(self.retention_days) is not int or self.retention_days <= 0:
            raise ValueError("field 'retention_days' must be a positive integer")


@dataclass(frozen=True, slots=True)
class JobLogContext:
    """Validated job identity and date partition for a job log file."""

    job_id: str
    run_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str):
            raise TypeError("field 'job_id' must be a string")
        if _JOB_ID_PATTERN.fullmatch(self.job_id) is None:
            raise ValueError(
                "field 'job_id' must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}"
            )
        if type(self.run_date) is not date:
            raise TypeError("field 'run_date' must be a date")


class ProcessLogger(Protocol):
    """Parameterized logging contract implemented by public ``src.logs``."""

    def debug(self, message: str, *args: object) -> None: ...

    def info(self, message: str, *args: object) -> None: ...

    def warning(self, message: str, *args: object) -> None: ...

    def error(self, message: str, *args: object) -> None: ...

    def exception(self, message: str, *args: object) -> None:
        """Record one traceback only at a recovery or termination boundary."""
        ...


@runtime_checkable
class _LoguruSinkRegistry(Protocol):
    """Structural boundary needed to release Loguru sinks by ID."""

    def remove(self, handler_id: int | None = None) -> None: ...


class LoggingSession(Protocol):
    """Lifecycle contract returned by process logging configuration functions."""

    def close(self) -> None:
        """Remove all owned sinks and retain failed IDs for a later retry."""
        ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class _OwnedLoggingSession:
    """Own sinks installed for the single active logging scope in a process.

    Application bootstrap code receives this owner from a ``configure_*``
    function. Ordinary modules should import only ``logs`` from ``src``.
    """

    def __init__(
        self,
        backend: object,
        *,
        sink_ids: Sequence[int],
    ) -> None:
        if not isinstance(backend, _LoguruSinkRegistry):
            raise TypeError("field 'backend' must implement Loguru sink removal")
        if not sink_ids:
            raise ValueError("field 'sink_ids' must contain at least one sink ID")
        if any(type(sink_id) is not int or sink_id < 0 for sink_id in sink_ids):
            raise ValueError("field 'sink_ids' must contain non-negative integers")
        if len(set(sink_ids)) != len(sink_ids):
            raise ValueError("field 'sink_ids' must not contain duplicates")

        self._backend = backend
        self._pending_sink_ids = set(sink_ids)
        self._is_closing = False
        self._is_closed = False

    def close(self) -> None:
        """Remove all owned sinks and retain failed IDs for a later retry."""
        if self._is_closed:
            return

        self._is_closing = True
        cleanup_errors: list[Exception] = []
        for sink_id in sorted(self._pending_sink_ids):
            try:
                self._backend.remove(sink_id)
            except Exception as error:
                cleanup_errors.append(error)
            else:
                self._pending_sink_ids.remove(sink_id)

        if not self._pending_sink_ids:
            self._is_closed = True
        if cleanup_errors:
            raise ExceptionGroup(
                "one or more Loguru sinks failed to close",
                cleanup_errors,
            )

    def __enter__(self) -> Self:
        if self._is_closed:
            raise RuntimeError("logging session is closed")
        if self._is_closing:
            raise RuntimeError("logging session is closing")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def configure_api_logging(settings: LoggingSettings) -> LoggingSession:
    """Route process-wide ``src.logs`` records to API file and console sinks."""
    return _configure_interactive_logging(
        log_file=settings.log_root / "api" / "api.current.log",
        settings=settings,
    )


def configure_system_logging(settings: LoggingSettings) -> LoggingSession:
    """Route process-wide ``src.logs`` records to system file and console sinks."""
    return _configure_interactive_logging(
        log_file=settings.log_root / "system.log",
        settings=settings,
    )


def configure_job_logging(
    settings: LoggingSettings,
    context: JobLogContext,
) -> LoggingSession:
    """Route process-wide ``src.logs`` records to one safe job log file."""
    log_file = (
        settings.log_root
        / "jobs"
        / context.run_date.isoformat()
        / f"{context.job_id}.log"
    )
    return _configure_file_logging(log_file=log_file, settings=settings)


def _configure_interactive_logging(
    *,
    log_file: Path,
    settings: LoggingSettings,
) -> LoggingSession:
    sink_ids = [_replace_with_file_sink(log_file=log_file, settings=settings)]
    try:
        sink_ids.append(
            _loguru_logger.add(
                sys.stderr,
                level=settings.level.value,
                format=_LOG_FORMAT,
                backtrace=True,
                diagnose=False,
            )
        )
    except Exception as initialization_error:
        _raise_after_sink_cleanup(
            sink_ids=sink_ids,
            initialization_error=initialization_error,
        )
    return _OwnedLoggingSession(_loguru_logger, sink_ids=sink_ids)


def _configure_file_logging(
    *,
    log_file: Path,
    settings: LoggingSettings,
) -> LoggingSession:
    sink_id = _replace_with_file_sink(log_file=log_file, settings=settings)
    return _OwnedLoggingSession(_loguru_logger, sink_ids=(sink_id,))


def _replace_with_file_sink(
    *,
    log_file: Path,
    settings: LoggingSettings,
) -> int:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    _loguru_logger.remove()
    return _loguru_logger.add(
        str(log_file),
        level=settings.level.value,
        format=_LOG_FORMAT,
        rotation="1 day",
        retention=f"{settings.retention_days} days",
        enqueue=True,
        backtrace=True,
        diagnose=False,
        encoding="utf-8",
    )


def _raise_after_sink_cleanup(
    *,
    sink_ids: Sequence[int],
    initialization_error: Exception,
) -> Never:
    cleanup_errors: list[Exception] = []
    for sink_id in sink_ids:
        try:
            _loguru_logger.remove(sink_id)
        except Exception as cleanup_error:
            cleanup_errors.append(cleanup_error)
    if cleanup_errors:
        raise ExceptionGroup(
            "Loguru initialization and cleanup failed",
            [initialization_error, *cleanup_errors],
        ) from initialization_error
    raise initialization_error


__all__ = [
    "JobLogContext",
    "LogLevel",
    "LoggingSession",
    "LoggingSettings",
    "ProcessLogger",
    "configure_api_logging",
    "configure_job_logging",
    "configure_system_logging",
]
