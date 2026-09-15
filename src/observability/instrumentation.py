# filepath: src/observability/instrumentation.py
from __future__ import annotations

import time
from collections.abc import Callable
from types import TracebackType
from typing import Literal, ParamSpec, Self, TypeVar

from src import logs

_Parameters = ParamSpec("_Parameters")
_ResultT = TypeVar("_ResultT")


class Instrumentation:
    """Measure and report accumulated named-operation runtimes.

    Example:
        with Instrumentation("training_2026-07") as instrumentation:
            result = instrumentation.measure("DatasetBuildStep", load, window)
    """

    def __init__(self, scope_name: str) -> None:
        """Create an empty timeline for one workflow execution.

        Example:
            instrumentation = Instrumentation("2026-07-20")
        """
        self._scope_name = scope_name
        self._total_seconds_by_step: dict[str, float] = {}
        self._runs_by_step: dict[str, int] = {}

    def __enter__(self) -> Self:
        """Return this instrumentation for one workflow execution.

        Example:
            with Instrumentation("2026-07-20") as instrumentation:
                instrumentation.measure("CalendarMaterializeStep", step.run, context)
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
        timeline_rows: list[str] = []
        total_seconds = 0.0
        for name, elapsed_seconds in self._total_seconds_by_step.items():
            runs = self._runs_by_step[name]
            if runs == 1:
                timeline_rows.append(f"{name:<35} {elapsed_seconds:>8.3f}s")
            else:
                average_seconds = elapsed_seconds / runs
                timeline_rows.append(
                    f"{name:<35} {elapsed_seconds:>8.3f}s "
                    f"avg={average_seconds:.3f}s runs={runs}"
                )
            total_seconds += elapsed_seconds

        timeline_rows.append(f"{'Total':<35} {total_seconds:>8.3f}s")
        timeline_rows.append("=" * 43)
        timeline = "\n".join(timeline_rows)
        if exc_type is None:
            logs.info(
                f"✅ ===== Pipeline timeline for {self._scope_name} =====\n"
                f"{timeline}"
            )
        else:
            logs.error(
                f"❌ ===== Pipeline timeline for {self._scope_name} =====\n"
                f"{timeline}"
            )
        return False

    def measure(
        self,
        operation_name: str,
        operation: Callable[_Parameters, _ResultT],
        *args: _Parameters.args,
        **kwargs: _Parameters.kwargs,
    ) -> _ResultT:
        """Call one operation and include its elapsed time in the timeline.

        Example:
            result = instrumentation.measure(
                "CalendarMaterializeStep",
                step.run,
                context,
            )
        """
        started_at = time.perf_counter()
        try:
            return operation(*args, **kwargs)
        finally:
            elapsed_seconds = time.perf_counter() - started_at
            self._total_seconds_by_step[operation_name] = (
                self._total_seconds_by_step.get(operation_name, 0.0) + elapsed_seconds
            )
            self._runs_by_step[operation_name] = (
                self._runs_by_step.get(operation_name, 0) + 1
            )
