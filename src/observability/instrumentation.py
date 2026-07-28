# filepath: src/observability/instrumentation.py
from __future__ import annotations

import time
from collections.abc import Callable
from types import TracebackType
from typing import Literal, TypeVar

from src import logs


_ContextT = TypeVar("_ContextT")
_ResultT = TypeVar("_ResultT")


class Instrumentation:
    """Measure and report accumulated concrete-step runtimes.

    Example:
        with Instrumentation("training_2026-07") as instrumentation:
            result = instrumentation.call(step, context)
    """

    def __init__(self, scope_name: str) -> None:
        """Create an empty timeline for one workflow execution.

        Example:
            instrumentation = Instrumentation("2026-07-20")
        """
        self._scope_name = scope_name
        self._total_seconds_by_step: dict[str, float] = {}
        self._runs_by_step: dict[str, int] = {}

    def __enter__(self) -> Instrumentation:
        """Return this instrumentation for one workflow execution.

        Example:
            with Instrumentation("2026-07-20") as instrumentation:
                instrumentation.call(step, context)
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Report the timeline and preserve any workflow exception.

        Example:
            with Instrumentation("2026-07-20"):
                pass
        """
        logs.info(
            f"[Timeline] ===== Pipeline timeline for {self._scope_name} ====="
        )
        total_seconds = 0.0
        for name, elapsed_seconds in self._total_seconds_by_step.items():
            runs = self._runs_by_step[name]
            average_seconds = elapsed_seconds / runs
            logs.info(
                f"[Timeline] {name:<35} {elapsed_seconds:>8.3f}s "
                f"avg={average_seconds:.3f}s runs={runs}"
            )
            total_seconds += elapsed_seconds
        logs.info(f"[Timeline] {'Total':<35} {total_seconds:>8.3f}s")
        logs.info(f"[Timeline] {'=' * 43}")
        return False

    def call(
        self,
        step: Callable[[_ContextT], _ResultT],
        context: _ContextT,
    ) -> _ResultT:
        """Call one step and include its elapsed time in the timeline.

        Example:
            result = instrumentation.call(step, context)
        """
        name = type(step).__name__
        started_at = time.perf_counter()
        try:
            return step(context)
        finally:
            elapsed_seconds = time.perf_counter() - started_at
            self._total_seconds_by_step[name] = (
                self._total_seconds_by_step.get(name, 0.0) + elapsed_seconds
            )
            self._runs_by_step[name] = self._runs_by_step.get(name, 0) + 1
