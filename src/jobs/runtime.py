# filepath: src/jobs/runtime.py
"""Own the process-local FIFO queue and child-process lifecycle."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import uuid
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import TypeAlias

from src import logs
from src.jobs.requests import (
    JOB_EXIT_CODE_SKIPPED,
    BacktestSubmission,
    DataSubmission,
    JobKind,
    JobSubmission,
    TrainingSubmission,
    build_cli_command,
)
from src.utils.datetime_utils import DateTimeUtils


MAX_RUNNING_JOBS = 2
CANCEL_GRACE_SECONDS = 10.0


class JobStatus(str, Enum):
    """List every externally visible state of one job."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class DataJobScope:
    """Identify the single natural day executed by a data job."""

    date: str


@dataclass(frozen=True, slots=True)
class RangeJobScope:
    """Identify the complete inclusive range executed by one job."""

    start: str
    end: str


JobScope: TypeAlias = DataJobScope | RangeJobScope


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """Expose the immutable public view of one process-local job."""

    job_id: str
    kind: JobKind
    scope: JobScope
    status: JobStatus
    submitted_at: str
    started_at: str | None
    finished_at: str | None


class JobNotFoundError(KeyError):
    """Report that a job ID is unknown to the current service process."""


class JobNotCancellableError(RuntimeError):
    """Report that a terminal non-cancelled job cannot be cancelled."""


@dataclass(slots=True)
class _Job:
    job_id: str
    submission: JobSubmission
    submitted_at: str
    status: JobStatus = JobStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    process: subprocess.Popen[bytes] | None = None
    waiter: threading.Thread | None = None


class JobRuntime:
    """Own one service process's unbounded FIFO queue and two execution slots."""

    def __init__(self, job_log_root: Path = Path("logs/jobs")) -> None:
        """Bind job log storage without starting work or creating files."""
        if not isinstance(job_log_root, Path):
            raise TypeError("job_log_root must be a pathlib.Path")

        self._job_log_root = job_log_root.resolve()
        self._jobs: dict[str, _Job] = {}
        self._pending_job_ids: deque[str] = deque()
        self._lock = threading.Lock()
        self._is_closed = False

    def __enter__(self) -> JobRuntime:
        """Return this runtime for one explicitly owned service lifetime."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Cancel and reap child processes when the service lifetime ends."""
        self.shutdown()

    def submit(self, submissions: Sequence[JobSubmission]) -> list[JobSnapshot]:
        """Atomically enqueue a complete non-empty request in FIFO order."""
        owned_submissions = tuple(submissions)
        if not owned_submissions:
            raise ValueError("submissions must not be empty")

        with self._lock:
            if self._is_closed:
                raise RuntimeError("job runtime is closed")

            submitted_at = DateTimeUtils.now().isoformat(timespec="microseconds")
            jobs = [
                _Job(
                    job_id=str(uuid.uuid4()),
                    submission=submission,
                    submitted_at=submitted_at,
                )
                for submission in owned_submissions
            ]
            for job in jobs:
                self._jobs[job.job_id] = job
                self._pending_job_ids.append(job.job_id)
                logs.info(
                    f"[JOB] accepted job_id={job.job_id} "
                    f"kind={job.submission.kind}"
                )
            self._dispatch_pending_locked()
            return [self._snapshot_locked(job) for job in jobs]

    def get(self, job_id: str) -> JobSnapshot:
        """Return the current public snapshot for one known job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            return self._snapshot_locked(job)

    def cancel(self, job_id: str) -> JobSnapshot:
        """Cancel a pending or running job according to its current state."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)

            if job.status is JobStatus.PENDING:
                self._pending_job_ids.remove(job_id)
                job.status = JobStatus.CANCELLED
                job.finished_at = DateTimeUtils.now().isoformat(
                    timespec="microseconds"
                )
                logs.info(
                    f"[JOB] transition job_id={job.job_id} "
                    f"from=PENDING to=CANCELLED"
                )
            elif job.status is JobStatus.RUNNING:
                self._begin_cancellation_locked(job)
            elif job.status is JobStatus.CANCELLING:
                pass
            elif job.status is not JobStatus.CANCELLED:
                raise JobNotCancellableError(job_id)

            return self._snapshot_locked(job)

    def shutdown(self) -> None:
        """Stop admission, discard pending work, and reap every child process."""
        with self._lock:
            if self._is_closed:
                return
            self._is_closed = True

            pending_count = len(self._pending_job_ids)
            self._pending_job_ids.clear()
            for job in self._jobs.values():
                if job.status is JobStatus.RUNNING:
                    self._begin_cancellation_locked(job)
            waiters = [
                job.waiter for job in self._jobs.values() if job.waiter is not None
            ]
            logs.info(
                f"[JOB] runtime.shutdown pending_discarded={pending_count} "
                f"active={len(waiters)}"
            )

        for waiter in waiters:
            waiter.join()

    def _dispatch_pending_locked(self) -> None:
        if self._is_closed:
            return

        running_count = sum(
            job.status in {JobStatus.RUNNING, JobStatus.CANCELLING}
            for job in self._jobs.values()
        )
        while running_count < MAX_RUNNING_JOBS and self._pending_job_ids:
            job_id = self._pending_job_ids.popleft()
            job = self._jobs[job_id]
            try:
                self._start_job_locked(job)
            except Exception:
                job.status = JobStatus.FAILED
                job.finished_at = DateTimeUtils.now().isoformat(
                    timespec="microseconds"
                )
                logs.exception(
                    f"[JOB] startup failed job_id={job.job_id} "
                    f"kind={job.submission.kind}"
                )
                continue
            running_count += 1

    def _start_job_locked(self, job: _Job) -> None:
        started_at = DateTimeUtils.now()
        log_path = (
            self._job_log_root
            / started_at.date().isoformat()
            / f"{job.job_id}.log"
        )
        command = build_cli_command(job.submission, job.job_id)
        process: subprocess.Popen[bytes] | None = None
        log_created = False

        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("xb") as log_file:
                log_created = True
                process = subprocess.Popen(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

            waiter = threading.Thread(
                target=self._wait_for_process,
                args=(job.job_id, process),
                daemon=True,
            )
            waiter.start()
        except Exception as exc:
            if process is None:
                if log_created:
                    try:
                        log_path.unlink()
                    except OSError as cleanup_error:
                        exc.add_note(
                            f"failed to remove unused job log: {cleanup_error}"
                        )
            else:
                self._reap_untracked_process(process, exc)
            raise

        job.process = process
        job.waiter = waiter
        job.started_at = started_at.isoformat(timespec="microseconds")
        job.status = JobStatus.RUNNING
        logs.info(
            f"[JOB] transition job_id={job.job_id} "
            f"from=PENDING to=RUNNING"
        )

    def _wait_for_process(
        self,
        job_id: str,
        process: subprocess.Popen[bytes],
    ) -> None:
        try:
            exit_code = process.wait()
        except Exception:
            logs.exception(f"[JOB] wait failed job_id={job_id}")
            with self._lock:
                job = self._jobs[job_id]
                job.status = JobStatus.FAILED
                job.finished_at = DateTimeUtils.now().isoformat(
                    timespec="microseconds"
                )
                job.process = None
                job.waiter = None
                self._dispatch_pending_locked()
            return

        with self._lock:
            job = self._jobs[job_id]
            old_status = job.status
            if old_status is JobStatus.CANCELLING:
                job.status = JobStatus.CANCELLED
            elif exit_code == 0:
                job.status = JobStatus.SUCCESS
            elif exit_code == JOB_EXIT_CODE_SKIPPED:
                job.status = JobStatus.SKIPPED
            else:
                job.status = JobStatus.FAILED
            job.finished_at = DateTimeUtils.now().isoformat(timespec="microseconds")
            job.process = None
            job.waiter = None
            logs.info(
                f"[JOB] transition job_id={job.job_id} "
                f"from={old_status.value} to={job.status.value} "
                f"exit_code={exit_code}"
            )
            self._dispatch_pending_locked()

    def _begin_cancellation_locked(self, job: _Job) -> None:
        process = job.process
        if process is None:
            raise RuntimeError("running job is missing its child process")

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        job.status = JobStatus.CANCELLING
        logs.info(
            f"[JOB] transition job_id={job.job_id} "
            f"from=RUNNING to=CANCELLING signal=SIGTERM"
        )
        force_cancel = threading.Timer(
            CANCEL_GRACE_SECONDS,
            self._force_cancel,
            args=(job.job_id, process),
        )
        force_cancel.daemon = True
        force_cancel.start()

    def _force_cancel(
        self,
        job_id: str,
        process: subprocess.Popen[bytes],
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if (
                job.status is not JobStatus.CANCELLING
                or job.process is not process
            ):
                return
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                logs.exception(
                    f"[JOB] forced cancellation failed job_id={job.job_id}"
                )
                return
            logs.info(
                f"[JOB] cancellation escalated job_id={job.job_id} signal=SIGKILL"
            )

    @staticmethod
    def _reap_untracked_process(
        process: subprocess.Popen[bytes],
        startup_error: Exception,
    ) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as cleanup_error:
            startup_error.add_note(
                f"failed to stop child after startup failure: {cleanup_error}"
            )
        try:
            process.wait()
        except Exception as cleanup_error:
            startup_error.add_note(
                f"failed to reap child after startup failure: {cleanup_error}"
            )

    @staticmethod
    def _snapshot_locked(job: _Job) -> JobSnapshot:
        submission = job.submission
        if isinstance(submission, DataSubmission):
            scope: JobScope = DataJobScope(date=submission.date)
        elif isinstance(submission, (TrainingSubmission, BacktestSubmission)):
            scope = RangeJobScope(
                start=submission.start,
                end=submission.end,
            )
        else:
            raise TypeError("unknown job submission type")

        return JobSnapshot(
            job_id=job.job_id,
            kind=submission.kind,
            scope=scope,
            status=job.status,
            submitted_at=job.submitted_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
