# filepath: src/jobs/runtime.py
"""Own the process-local FIFO queue and child-process lifecycle."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import TracebackType

from src import logs
from src.jobs.requests import (
    JOB_EXIT_CODE_SKIPPED,
    JobKind,
    JobSubmission,
    build_cli_command,
)
from src.utils.datetime_utils import DateTimeUtils


MAX_RUNNING_JOBS = 2
CANCEL_GRACE_SECONDS = 10.0


class JobStatus(str, Enum):
    """List every externally visible state of one job.

    Example:
        status = JobStatus.PENDING
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class RangeJobScope:
    """Identify the complete inclusive range executed by one job.

    Example:
        scope = RangeJobScope(start="2026-07-01", end="2026-07-20")
    """

    start: str
    end: str


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """Expose the immutable public view of one process-local job.

    Example:
        snapshot = JobSnapshot(
            job_id="00000000-0000-4000-8000-000000000001",
            kind="data-standard",
            scope=RangeJobScope(
                start="2026-07-20",
                end="2026-07-20",
            ),
            status=JobStatus.PENDING,
            submitted_at="2026-07-20T09:30:00.000000+08:00",
            started_at=None,
            finished_at=None,
        )
    """

    job_id: str
    kind: JobKind
    scope: RangeJobScope
    status: JobStatus
    submitted_at: str
    started_at: str | None
    finished_at: str | None


class JobNotFoundError(KeyError):
    """Report that a job ID is unknown to the current service process.

    Example:
        error = JobNotFoundError("00000000-0000-4000-8000-000000000001")
    """


class JobNotCancellableError(RuntimeError):
    """Report that a terminal non-cancelled job cannot be cancelled.

    Example:
        error = JobNotCancellableError(
            "00000000-0000-4000-8000-000000000001"
        )
    """


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
    cancel_timer: threading.Timer | None = None


class JobRuntime:
    """Own one service process's unbounded FIFO queue and two execution slots.

    Example:
        runtime = JobRuntime(Path("logs/jobs"))
        runtime.close()
    """

    def __init__(
        self,
        job_log_root: Path = Path("logs/jobs"),
        *,
        clock: Callable[[], datetime] = DateTimeUtils.now,
        job_id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        """Bind job log storage without starting work or creating files."""
        if not isinstance(job_log_root, Path):
            raise TypeError("job_log_root must be a pathlib.Path")

        self._job_log_root = job_log_root.resolve()
        self._clock = clock
        self._job_id_factory = job_id_factory
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
        self.close()

    def submit(self, submissions: Sequence[JobSubmission]) -> list[JobSnapshot]:
        """Atomically enqueue a complete non-empty request in FIFO order.

        Example:
            with JobRuntime(Path("logs/jobs")) as runtime:
                jobs = runtime.submit(
                    [
                        DataSubmission(
                            kind="data-standard",
                            start="2026-07-20",
                            end="2026-07-20",
                        )
                    ]
                )
        """
        owned_submissions = tuple(submissions)
        if not owned_submissions:
            raise ValueError("submissions must not be empty")

        with self._lock:
            if self._is_closed:
                raise RuntimeError("job runtime is closed")

            submitted_at = self._clock().isoformat(timespec="microseconds")
            jobs = [
                _Job(
                    job_id=str(self._job_id_factory()),
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
        """Return the current public snapshot for one known job.

        Example:
            with JobRuntime(Path("logs/jobs")) as runtime:
                job = runtime.submit(
                    [
                        DataSubmission(
                            kind="data-standard",
                            start="2026-07-20",
                            end="2026-07-20",
                        )
                    ]
                )[0]
                snapshot = runtime.get(job.job_id)
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            return self._snapshot_locked(job)

    def cancel(self, job_id: str) -> JobSnapshot:
        """Cancel a pending or running job according to its current state.

        Example:
            with JobRuntime(Path("logs/jobs")) as runtime:
                job = runtime.submit(
                    [
                        DataSubmission(
                            kind="data-standard",
                            start="2026-07-20",
                            end="2026-07-20",
                        )
                    ]
                )[0]
                cancelled = runtime.cancel(job.job_id)
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)

            if job.status is JobStatus.PENDING:
                self._pending_job_ids.remove(job_id)
                job.status = JobStatus.CANCELLED
                job.finished_at = self._clock().isoformat(
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

    def close(self) -> None:
        """Stop admission, discard pending work, and reap every child process.

        Example:
            runtime = JobRuntime(Path("logs/jobs"))
            runtime.close()
        """
        with self._lock:
            has_resources = any(
                job.process is not None
                or job.waiter is not None
                or job.cancel_timer is not None
                for job in self._jobs.values()
            )
            if self._is_closed and not self._pending_job_ids and not has_resources:
                return
            self._is_closed = True

            pending_count = len(self._pending_job_ids)
            self._pending_job_ids.clear()
            for job in self._jobs.values():
                if job.status is JobStatus.RUNNING:
                    self._begin_cancellation_locked(job)
            waiters: list[threading.Thread] = []
            for job in self._jobs.values():
                waiter = job.waiter
                if waiter is not None:
                    waiters.append(waiter)
            active_count = sum(
                job.process is not None for job in self._jobs.values()
            )
            logs.info(
                f"[JOB] runtime.close pending_discarded={pending_count} "
                f"active={active_count}"
            )

        for waiter in waiters:
            waiter.join()

        with self._lock:
            timers: list[tuple[_Job, threading.Timer]] = []
            remaining_processes: list[
                tuple[_Job, subprocess.Popen[bytes]]
            ] = []
            for job in self._jobs.values():
                cancel_timer = job.cancel_timer
                if cancel_timer is not None:
                    timers.append((job, cancel_timer))
                process = job.process
                if process is not None:
                    remaining_processes.append((job, process))
            for _, cancel_timer in timers:
                cancel_timer.cancel()

        for _, cancel_timer in timers:
            cancel_timer.join()

        for job, process in remaining_processes:
            try:
                exit_code = self._kill_and_reap_process(process)
            except Exception as error:
                logs.opt(exception=error).error(
                    f"[JOB] close reap failed job_id={job.job_id}"
                )
                continue

            with self._lock:
                if job.process is not process:
                    continue
                old_status = job.status
                job.status = (
                    JobStatus.CANCELLED
                    if old_status is JobStatus.CANCELLING
                    else JobStatus.FAILED
                )
                job.finished_at = self._clock().isoformat(timespec="microseconds")
                job.process = None
                job.waiter = None
                job.cancel_timer = None
                logs.info(
                    f"[JOB] transition job_id={job.job_id} "
                    f"from={old_status.value} to={job.status.value} "
                    f"exit_code={exit_code}"
                )

        with self._lock:
            for job, cancel_timer in timers:
                if job.cancel_timer is cancel_timer:
                    job.cancel_timer = None

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
                if job.process is not None:
                    self._is_closed = True
                    raise
                job.status = JobStatus.FAILED
                job.finished_at = self._clock().isoformat(
                    timespec="microseconds"
                )
                logs.exception(
                    f"[JOB] startup failed job_id={job.job_id} "
                    f"kind={job.submission.kind}"
                )
                continue
            running_count += 1

    def _start_job_locked(self, job: _Job) -> None:
        started_at = self._clock()
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
                log_file.write(
                    f"log_file={log_path.name}\n".encode("utf-8")
                )
                log_file.flush()
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
                try:
                    self._kill_and_reap_process(process)
                except Exception as cleanup_error:
                    job.process = process
                    raise RuntimeError(
                        f"job startup cleanup failed; job_id={job.job_id}"
                    ) from cleanup_error
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
        wait_failed = False
        try:
            exit_code = process.wait()
        except Exception as wait_error:
            try:
                exit_code = self._kill_and_reap_process(process)
            except Exception as reap_error:
                wait_error.add_note(f"failed to reap child: {reap_error}")
                with self._lock:
                    job = self._jobs[job_id]
                    if job.process is process:
                        self._is_closed = True
                logs.opt(exception=wait_error).error(
                    f"[JOB] wait/reap failed job_id={job_id} "
                    "runtime_closed=true"
                )
                return
            logs.opt(exception=wait_error).error(
                f"[JOB] wait failed job_id={job_id} child_reaped=true"
            )
            wait_failed = True

        cancel_timer: threading.Timer | None
        with self._lock:
            job = self._jobs[job_id]
            old_status = job.status
            if old_status is JobStatus.CANCELLING:
                job.status = JobStatus.CANCELLED
            elif wait_failed:
                job.status = JobStatus.FAILED
            elif exit_code == 0:
                job.status = JobStatus.SUCCESS
            elif exit_code == JOB_EXIT_CODE_SKIPPED:
                job.status = JobStatus.SKIPPED
            else:
                job.status = JobStatus.FAILED
            job.finished_at = self._clock().isoformat(timespec="microseconds")
            job.process = None
            job.waiter = None
            cancel_timer = job.cancel_timer
            if cancel_timer is not None:
                cancel_timer.cancel()
            job.cancel_timer = None
            logs.info(
                f"[JOB] transition job_id={job.job_id} "
                f"from={old_status.value} to={job.status.value} "
                f"exit_code={exit_code}"
            )
            self._dispatch_pending_locked()

        if cancel_timer is not None:
            cancel_timer.join()

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
        job.cancel_timer = force_cancel
        try:
            force_cancel.start()
        except Exception as timer_error:
            job.cancel_timer = None
            self._is_closed = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as kill_error:
                timer_error.add_note(
                    f"failed to force-stop child: {kill_error}"
                )
            logs.opt(exception=timer_error).error(
                f"[JOB] cancellation timer failed job_id={job.job_id} "
                "runtime_closed=true signal=SIGKILL"
            )

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
    def _kill_and_reap_process(
        process: subprocess.Popen[bytes],
    ) -> int:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.wait()

    @staticmethod
    def _snapshot_locked(job: _Job) -> JobSnapshot:
        submission = job.submission

        return JobSnapshot(
            job_id=job.job_id,
            kind=submission.kind,
            scope=RangeJobScope(
                start=submission.start,
                end=submission.end,
            ),
            status=job.status,
            submitted_at=job.submitted_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
