# filepath: tests/utils/test_download_utils.py
from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Self

import pytest

from src.utils.download_utils import DownloadProgress


@dataclass(frozen=True, slots=True)
class RecordedLogCall:
    message: str
    args: tuple[object, ...]


class RecordingLogger:
    """Record parameterized logger calls made by DownloadProgress."""

    def __init__(self) -> None:
        self.messages: list[RecordedLogCall] = []

    def debug(self, message: str, *args: object) -> None:
        self._record(message, *args)

    def info(self, message: str, *args: object) -> None:
        self._record(message, *args)

    def warning(self, message: str, *args: object) -> None:
        self._record(message, *args)

    def error(self, message: str, *args: object) -> None:
        self._record(message, *args)

    def exception(self, message: str, *args: object) -> None:
        self._record(message, *args)

    def close(self) -> None:
        return

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _record(self, message: str, *args: object) -> None:
        self.messages.append(RecordedLogCall(message=message, args=args))


def test_download_progress_update_before_interval_is_quiet() -> None:
    logger = RecordingLogger()
    times = iter([100.0, 100.5])
    progress = DownloadProgress(
        total_bytes=100,
        filename="payload.csv.7z",
        logger=logger,
        report_interval_seconds=1.0,
        monotonic_clock=lambda: next(times),
    )

    progress.update(25)

    assert progress.downloaded_bytes == 25
    assert logger.messages == []


def test_download_progress_update_uses_parameterized_operational_fields() -> None:
    logger = RecordingLogger()
    times = iter([100.0, 101.5])
    progress = DownloadProgress(
        total_bytes=100,
        filename="payload.csv.7z",
        logger=logger,
        report_interval_seconds=1.0,
        monotonic_clock=lambda: next(times),
    )

    progress.update(50)

    assert logger.messages == [
        RecordedLogCall(
            message="download progress; filename={} status={}",
            args=(
                "payload.csv.7z",
                "percent=50.00% speed=33.33 B/s eta=00:01",
            ),
        )
    ]


def test_download_progress_finish_distinguishes_unknown_total() -> None:
    logger = RecordingLogger()
    times = iter([10.0, 10.1, 12.0])
    progress = DownloadProgress(
        total_bytes=None,
        filename="payload.csv.7z",
        logger=logger,
        report_interval_seconds=100.0,
        monotonic_clock=lambda: next(times),
    )

    progress.update(2048)
    progress.finish()

    assert logger.messages == [
        RecordedLogCall(
            message="download complete; filename={} status={}",
            args=(
                "payload.csv.7z",
                "downloaded=2.00 KB speed=1.00 KB/s eta=unknown",
            ),
        )
    ]


def test_download_progress_zero_total_is_a_known_empty_download() -> None:
    logger = RecordingLogger()
    times = iter([20.0, 21.0])
    progress = DownloadProgress(
        total_bytes=0,
        filename="empty.csv",
        logger=logger,
        monotonic_clock=lambda: next(times),
    )

    progress.finish()

    assert logger.messages == [
        RecordedLogCall(
            message="download complete; filename={} status={}",
            args=(
                "empty.csv",
                "percent=100.00% speed=0.00 B/s eta=00:00",
            ),
        )
    ]


@pytest.mark.parametrize(
    ("chunk_size_bytes", "error_type"),
    [
        (-1, ValueError),
        (1.5, TypeError),
        (True, TypeError),
    ],
)
def test_download_progress_rejects_invalid_chunk_sizes(
    chunk_size_bytes: object,
    error_type: type[Exception],
) -> None:
    progress = DownloadProgress(
        total_bytes=100,
        filename="payload.csv.7z",
        logger=RecordingLogger(),
        monotonic_clock=lambda: 0.0,
    )

    with pytest.raises(error_type, match="chunk_size_bytes"):
        # Deliberately violate the static contract to verify boundary validation.
        progress.update(chunk_size_bytes)  # type: ignore[arg-type]


@pytest.mark.parametrize("filename", ["", "line\nbreak", "line\rbreak", "tab\tname"])
def test_download_progress_rejects_unsafe_filenames(filename: str) -> None:
    with pytest.raises(ValueError, match="filename"):
        DownloadProgress(
            total_bytes=100,
            filename=filename,
            logger=RecordingLogger(),
            monotonic_clock=lambda: 0.0,
        )
