# filepath: tests/jobs/test_runtime.py
"""FIFO, concurrency, completion, and cancellation contract tests."""

from __future__ import annotations

import signal
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, cast

import pytest

import src.jobs.runtime as runtime_module
from src.jobs.requests import DataSubmission
from src.jobs.runtime import JobRuntime, JobStatus


_FIXED_NOW = datetime.fromisoformat("2026-07-20T09:30:00.000000+08:00")


class _ControlledProcess:
    _next_pid = 10_000

    def __init__(self) -> None:
        self.pid = self._next_pid
        type(self)._next_pid += 1
        self._finished = threading.Event()
        self._exit_code = 0

    def finish(self, exit_code: int) -> None:
        self._exit_code = exit_code
        self._finished.set()

    def wait(self) -> int:
        if not self._finished.wait(timeout=5.0):
            raise TimeoutError("controlled child did not finish")
        return self._exit_code


class _ProcessFactory:
    def __init__(self) -> None:
        self.processes: list[_ControlledProcess] = []

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        stdout: object,
        stderr: int,
        start_new_session: bool,
    ) -> _ControlledProcess:
        cast(BinaryIO, stdout).write(b"child output\n")
        process = _ControlledProcess()
        self.processes.append(process)
        return process


def _wait_for_status(
    runtime: JobRuntime,
    job_id: str,
    status: JobStatus,
) -> None:
    deadline = time.monotonic() + 5.0
    while runtime.get(job_id).status is not status:
        if time.monotonic() >= deadline:
            raise AssertionError(f"job did not reach {status.value}")
        time.sleep(0.001)


def test_ten_jobs_are_accepted_with_two_running_and_fifo_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_factory = _ProcessFactory()
    processes_by_pid: dict[int, _ControlledProcess] = {}

    def popen(*args: object, **kwargs: object) -> _ControlledProcess:
        process = process_factory(*args, **kwargs)
        processes_by_pid[process.pid] = process
        return process

    def kill_process_group(pid: int, sent_signal: int) -> None:
        processes_by_pid[pid].finish(-sent_signal)

    monkeypatch.setattr(runtime_module.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime_module.os, "killpg", kill_process_group)
    monkeypatch.setattr(
        runtime_module,
        "build_cli_command",
        lambda submission, job_id: ("controlled-child",),
    )
    job_ids = iter(uuid.UUID(int=value) for value in range(1, 11))
    runtime = JobRuntime(
        tmp_path,
        clock=lambda: _FIXED_NOW,
        job_id_factory=lambda: next(job_ids),
    )
    submissions = [
        DataSubmission(kind="data-standard", date=f"2026-07-{day:02d}")
        for day in range(1, 11)
    ]

    jobs = runtime.submit(submissions)

    assert [job.status for job in jobs[:2]] == [
        JobStatus.RUNNING,
        JobStatus.RUNNING,
    ]
    assert all(job.status is JobStatus.PENDING for job in jobs[2:])
    assert [job.job_id for job in jobs] == [
        str(uuid.UUID(int=value)) for value in range(1, 11)
    ]
    assert all(
        job.submitted_at == "2026-07-20T09:30:00.000000+08:00"
        for job in jobs
    )
    assert len(process_factory.processes) == 2
    for job in jobs[:2]:
        job_log = next(tmp_path.rglob(f"{job.job_id}.log"))
        assert job_log.read_text(encoding="utf-8") == (
            f"log_file={job.job_id}.log\n"
            "child output\n"
        )

    process_factory.processes[0].finish(0)
    _wait_for_status(runtime, jobs[2].job_id, JobStatus.RUNNING)
    assert len(process_factory.processes) == 3

    cancelled = runtime.cancel(jobs[-1].job_id)
    assert cancelled.status is JobStatus.CANCELLED
    assert not list(tmp_path.rglob(f"{jobs[-1].job_id}.log"))

    runtime.close()


def test_running_cancel_waits_for_process_exit_before_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_factory = _ProcessFactory()
    processes_by_pid: dict[int, _ControlledProcess] = {}

    def popen(*args: object, **kwargs: object) -> _ControlledProcess:
        process = process_factory(*args, **kwargs)
        processes_by_pid[process.pid] = process
        return process

    def kill_process_group(pid: int, sent_signal: int) -> None:
        assert sent_signal == signal.SIGTERM
        processes_by_pid[pid].finish(-sent_signal)

    monkeypatch.setattr(runtime_module.subprocess, "Popen", popen)
    monkeypatch.setattr(runtime_module.os, "killpg", kill_process_group)
    monkeypatch.setattr(
        runtime_module,
        "build_cli_command",
        lambda submission, job_id: ("controlled-child",),
    )
    runtime = JobRuntime(
        tmp_path,
        clock=lambda: _FIXED_NOW,
        job_id_factory=lambda: uuid.UUID(int=1),
    )
    job = runtime.submit(
        [DataSubmission(kind="data-standard", date="2026-07-20")]
    )[0]

    cancelling = runtime.cancel(job.job_id)

    assert cancelling.status is JobStatus.CANCELLING
    _wait_for_status(runtime, job.job_id, JobStatus.CANCELLED)
    runtime.close()


def test_wait_failure_kills_and_reaps_before_marking_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WaitFailsOnceProcess:
        pid = 10_000

        def __init__(self) -> None:
            self.wait_calls = 0

        def wait(self) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise OSError("initial wait failed")
            return -signal.SIGKILL

    process = WaitFailsOnceProcess()
    sent_signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        runtime_module.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        runtime_module.os,
        "killpg",
        lambda pid, sent_signal: sent_signals.append((pid, sent_signal)),
    )
    monkeypatch.setattr(
        runtime_module,
        "build_cli_command",
        lambda submission, job_id: ("controlled-child",),
    )
    runtime = JobRuntime(
        tmp_path,
        clock=lambda: _FIXED_NOW,
        job_id_factory=lambda: uuid.UUID(int=1),
    )

    job = runtime.submit(
        [DataSubmission(kind="data-standard", date="2026-07-20")]
    )[0]

    _wait_for_status(runtime, job.job_id, JobStatus.FAILED)
    assert process.wait_calls == 2
    assert sent_signals == [(process.pid, signal.SIGKILL)]
    runtime.close()


def test_startup_cleanup_failure_closes_runtime_with_job_nonterminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProcessCannotBeReaped:
        pid = 10_000

        def wait(self) -> int:
            raise OSError("reap failed")

    class ThreadStartFails:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread start failed")

    process = ProcessCannotBeReaped()
    sent_signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        runtime_module.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(runtime_module.threading, "Thread", ThreadStartFails)
    monkeypatch.setattr(
        runtime_module.os,
        "killpg",
        lambda pid, sent_signal: sent_signals.append((pid, sent_signal)),
    )
    monkeypatch.setattr(
        runtime_module,
        "build_cli_command",
        lambda submission, job_id: ("controlled-child",),
    )
    runtime = JobRuntime(
        tmp_path,
        clock=lambda: _FIXED_NOW,
        job_id_factory=lambda: uuid.UUID(int=1),
    )

    with pytest.raises(
        RuntimeError,
        match="job startup cleanup failed",
    ):
        runtime.submit(
            [DataSubmission(kind="data-standard", date="2026-07-20")]
        )

    retained = runtime.get(str(uuid.UUID(int=1)))
    assert retained.status is JobStatus.PENDING
    assert retained.started_at is None
    assert retained.finished_at is None
    assert sent_signals == [(process.pid, signal.SIGKILL)]
    with pytest.raises(RuntimeError, match="job runtime is closed"):
        runtime.submit(
            [DataSubmission(kind="data-standard", date="2026-07-21")]
        )
    runtime.close()


def test_second_reap_failure_closes_only_runtime_and_retains_nonterminal_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_closed = threading.Event()

    class WaitFailsTwiceProcess:
        pid = 10_000

        def __init__(self) -> None:
            self.wait_calls = 0

        def wait(self) -> int:
            self.wait_calls += 1
            if self.wait_calls <= 2:
                raise OSError(f"wait failed #{self.wait_calls}")
            return -signal.SIGKILL

    class RuntimeLogger:
        def info(self, message: str) -> None:
            pass

        def exception(self, message: str) -> None:
            pass

        def opt(self, *, exception: Exception) -> RuntimeLogger:
            return self

        def error(self, message: str) -> None:
            if "runtime_closed=true" in message:
                runtime_closed.set()

    process = WaitFailsTwiceProcess()
    monkeypatch.setattr(runtime_module, "logs", RuntimeLogger())
    monkeypatch.setattr(
        runtime_module.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(runtime_module.os, "killpg", lambda pid, sent_signal: None)
    monkeypatch.setattr(
        runtime_module,
        "build_cli_command",
        lambda submission, job_id: ("controlled-child",),
    )
    runtime = JobRuntime(
        tmp_path,
        clock=lambda: _FIXED_NOW,
        job_id_factory=lambda: uuid.UUID(int=1),
    )
    job = runtime.submit(
        [DataSubmission(kind="data-standard", date="2026-07-20")]
    )[0]

    assert runtime_closed.wait(timeout=5.0)
    retained = runtime.get(job.job_id)
    assert retained.status is JobStatus.RUNNING
    assert retained.finished_at is None
    with pytest.raises(RuntimeError, match="job runtime is closed"):
        runtime.submit(
            [DataSubmission(kind="data-standard", date="2026-07-21")]
        )

    runtime.close()
    assert runtime.get(job.job_id).status is JobStatus.CANCELLED


def test_cancellation_timer_start_failure_force_stops_job_and_closes_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimerStartFails:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.daemon = False

        def start(self) -> None:
            raise RuntimeError("timer start failed")

    process = _ControlledProcess()
    sent_signals: list[int] = []

    def kill_process_group(pid: int, sent_signal: int) -> None:
        sent_signals.append(sent_signal)
        if sent_signal == signal.SIGKILL:
            process.finish(-sent_signal)

    monkeypatch.setattr(
        runtime_module.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(runtime_module.os, "killpg", kill_process_group)
    monkeypatch.setattr(runtime_module.threading, "Timer", TimerStartFails)
    monkeypatch.setattr(
        runtime_module,
        "build_cli_command",
        lambda submission, job_id: ("controlled-child",),
    )
    runtime = JobRuntime(
        tmp_path,
        clock=lambda: _FIXED_NOW,
        job_id_factory=lambda: uuid.UUID(int=1),
    )
    job = runtime.submit(
        [DataSubmission(kind="data-standard", date="2026-07-20")]
    )[0]

    cancelling = runtime.cancel(job.job_id)

    assert cancelling.status is JobStatus.CANCELLING
    _wait_for_status(runtime, job.job_id, JobStatus.CANCELLED)
    assert sent_signals == [signal.SIGTERM, signal.SIGKILL]
    with pytest.raises(RuntimeError, match="job runtime is closed"):
        runtime.submit(
            [DataSubmission(kind="data-standard", date="2026-07-21")]
        )
    runtime.close()
