# filepath: tests/utils/test_download_utils.py

from __future__ import annotations

import math

import pytest

from src.utils.download_utils import DownloadProgress


class RecordingLogger:
    """Record informational messages emitted by DownloadProgress."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        """Append one formatted message."""
        self.messages.append(message)


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

    assert logger.messages == []


def test_download_progress_update_reports_percent_speed_and_eta() -> None:
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
        "progress filename=payload.csv.7z "
        "status=percent=50.00% speed=33.33 B/s eta=00:01"
    ]


def test_download_progress_finish_reports_unknown_total() -> None:
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
        "complete filename=payload.csv.7z "
        "status=downloaded=2.00 KiB speed=1.00 KiB/s eta=unknown"
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
        "complete filename=empty.csv "
        "status=percent=100.00% speed=0.00 B/s eta=00:00"
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


@pytest.mark.parametrize("report_interval_seconds", [0, -1, math.inf, math.nan])
def test_download_progress_rejects_invalid_report_intervals(
    report_interval_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="report_interval_seconds"):
        DownloadProgress(
            total_bytes=100,
            filename="payload.csv.7z",
            logger=RecordingLogger(),
            report_interval_seconds=report_interval_seconds,
            monotonic_clock=lambda: 0.0,
        )
