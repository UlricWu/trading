# filepath: src/observability/timeline_reporter.py
from __future__ import annotations

from collections.abc import Mapping

from src import logs


class TimelineReporter:
    """Write an accumulated runtime timeline to operational logs."""

    def __init__(self, timeline: Mapping[str, float], scope_name: str) -> None:
        self.timeline = timeline
        self.scope_name = scope_name

    def report(self) -> None:
        logs.info(f"[Timeline] start scope={self.scope_name}")
        total_seconds = 0.0
        for name, elapsed_seconds in self.timeline.items():
            logs.info(
                f"[Timeline] entry name={name} "
                f"elapsed_seconds={elapsed_seconds:.3f}"
            )
            total_seconds += elapsed_seconds
        logs.info(
            f"[Timeline] finished scope={self.scope_name} "
            f"total_seconds={total_seconds:.3f}"
        )
