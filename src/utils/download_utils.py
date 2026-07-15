# filepath: src/utils/download_utils.py
from __future__ import annotations

import math
import time
from collections.abc import Callable

from src.utils.filesystem import FileSystem
from src.utils.logger import ProcessLogger

_MIN_ELAPSED_SECONDS = 1e-6
_ASCII_CONTROL_CHARACTER_LIMIT = 32
_ASCII_DELETE_CHARACTER = 127
_BINARY_UNIT_BASE = 1024


class DownloadProgress:
    """Aggregate byte counts and emit bounded download progress records.

    ``total_bytes=None`` means the source did not provide a total. The caller
    owns the injected logger; this object never closes it.
    """

    def __init__(
        self,
        total_bytes: int | None,
        filename: str,
        *,
        logger: ProcessLogger,
        report_interval_seconds: float = 5.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if total_bytes is not None and type(total_bytes) is not int:
            raise TypeError("field 'total_bytes' must be an integer or None")
        if total_bytes is not None and total_bytes < 0:
            raise ValueError("field 'total_bytes' must be non-negative")
        if not isinstance(filename, str):
            raise TypeError("field 'filename' must be a string")
        if not filename:
            raise ValueError("field 'filename' must be a non-empty string")
        if any(
            ord(character) < _ASCII_CONTROL_CHARACTER_LIMIT
            or ord(character) == _ASCII_DELETE_CHARACTER
            for character in filename
        ):
            raise ValueError("field 'filename' must not contain control characters")
        if isinstance(report_interval_seconds, bool) or not isinstance(
            report_interval_seconds,
            (int, float),
        ):
            raise TypeError("field 'report_interval_seconds' must be numeric")
        if not math.isfinite(report_interval_seconds) or report_interval_seconds <= 0:
            raise ValueError(
                "field 'report_interval_seconds' must be finite and positive"
            )
        if not callable(monotonic_clock):
            raise TypeError("field 'monotonic_clock' must be callable")

        self._total_bytes = total_bytes
        self._filename = filename
        self._downloaded_bytes = 0
        self._report_interval_seconds = float(report_interval_seconds)
        self._logger = logger
        self._monotonic_clock = monotonic_clock
        self._started_at_seconds = self._read_clock_seconds()
        self._last_reported_at_seconds = self._started_at_seconds

    @property
    def total_bytes(self) -> int | None:
        """Return the explicit total, or ``None`` when the source omitted it."""
        return self._total_bytes

    @property
    def filename(self) -> str:
        """Return the validated filename used as non-sensitive log context."""
        return self._filename

    @property
    def downloaded_bytes(self) -> int:
        """Return the accumulated byte count."""
        return self._downloaded_bytes

    def update(self, chunk_size_bytes: int) -> None:
        """Add a non-negative chunk and report after the configured interval."""
        if type(chunk_size_bytes) is not int:
            raise TypeError("field 'chunk_size_bytes' must be an integer")
        if chunk_size_bytes < 0:
            raise ValueError("field 'chunk_size_bytes' must be non-negative")

        self._downloaded_bytes += chunk_size_bytes
        now_seconds = self._read_clock_seconds()
        elapsed_since_report_seconds = now_seconds - self._last_reported_at_seconds
        if elapsed_since_report_seconds < self._report_interval_seconds:
            return

        self._last_reported_at_seconds = now_seconds
        self._logger.info(
            "download progress; filename={} status={}",
            self._filename,
            self._format_status(now_seconds=now_seconds),
        )

    def finish(self) -> None:
        """Emit one final aggregate progress record."""
        now_seconds = self._read_clock_seconds()
        self._logger.info(
            "download complete; filename={} status={}",
            self._filename,
            self._format_status(now_seconds=now_seconds),
        )

    def _read_clock_seconds(self) -> float:
        clock_value = self._monotonic_clock()
        if isinstance(clock_value, bool) or not isinstance(clock_value, (int, float)):
            raise TypeError("monotonic_clock must return a numeric value")
        if not math.isfinite(clock_value):
            raise ValueError("monotonic_clock must return a finite value")
        return float(clock_value)

    def _format_status(self, *, now_seconds: float) -> str:
        elapsed_seconds = max(
            now_seconds - self._started_at_seconds,
            _MIN_ELAPSED_SECONDS,
        )
        bytes_per_second = self._downloaded_bytes / elapsed_seconds
        speed_text = self._format_speed(bytes_per_second)

        if self._total_bytes is None:
            return (
                f"downloaded={FileSystem.format_size(self._downloaded_bytes)} "
                f"speed={speed_text} eta=unknown"
            )

        percent = (
            100.0
            if self._total_bytes == 0
            else min(self._downloaded_bytes / self._total_bytes * 100, 100.0)
        )
        remaining_bytes = max(self._total_bytes - self._downloaded_bytes, 0)
        eta_seconds = remaining_bytes / bytes_per_second if bytes_per_second > 0 else 0
        return (
            f"percent={percent:.2f}% speed={speed_text} "
            f"eta={self._format_eta(eta_seconds)}"
        )

    @staticmethod
    def _format_speed(bytes_per_second: float) -> str:
        if bytes_per_second >= _BINARY_UNIT_BASE**2:
            return f"{bytes_per_second / _BINARY_UNIT_BASE**2:.2f} MB/s"
        if bytes_per_second >= _BINARY_UNIT_BASE:
            return f"{bytes_per_second / _BINARY_UNIT_BASE:.2f} KB/s"
        return f"{bytes_per_second:.2f} B/s"

    @staticmethod
    def _format_eta(eta_seconds: float) -> str:
        whole_seconds = int(eta_seconds)
        hours, seconds_after_hours = divmod(whole_seconds, 3600)
        minutes, seconds = divmod(seconds_after_hours, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"


__all__ = ["DownloadProgress"]
