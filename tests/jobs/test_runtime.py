# filepath: tests/jobs/test_runtime.py
"""FIFO, concurrency, completion, and cancellation contract tests."""

from __future__ import annotations

import signal
import threading
import time
from pathlib import Path

import pytest

import src.jobs.runtime as runtime_module
from src.jobs.requests import DataSubmission
from src.jobs.runtime import JobRuntime, JobStatus


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
    runtime = JobRuntime(tmp_path)
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
    assert len(process_factory.processes) == 2

    process_factory.processes[0].finish(0)
    _wait_for_status(runtime, jobs[2].job_id, JobStatus.RUNNING)
    assert len(process_factory.processes) == 3

    cancelled = runtime.cancel(jobs[-1].job_id)
    assert cancelled.status is JobStatus.CANCELLED
    assert not list(tmp_path.rglob(f"{jobs[-1].job_id}.log"))

    runtime.shutdown()


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
    runtime = JobRuntime(tmp_path)
    job = runtime.submit(
        [DataSubmission(kind="data-standard", date="2026-07-20")]
    )[0]

    cancelling = runtime.cancel(job.job_id)

    assert cancelling.status is JobStatus.CANCELLING
    _wait_for_status(runtime, job.job_id, JobStatus.CANCELLED)
    runtime.shutdown()
