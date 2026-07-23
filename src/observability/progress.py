# filepath: src/observability/progress.py
from __future__ import annotations

from src import logs


class ProgressReporter:
    """Minimal aggregated progress reporter without a terminal UI dependency."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def start(self, task: str, total: int, unit: str = "") -> None:
        if self.enabled:
            logs.info(
                f"[Progress] started task={task} total={total} unit={unit or 'items'}"
            )

    def update(self, task: str, current: int, total: int, unit: str = "") -> None:
        if self.enabled:
            logs.info(
                f"[Progress] updated task={task} current={current} "
                f"total={total} unit={unit or 'items'}"
            )

    def done(self, task: str) -> None:
        if self.enabled:
            logs.info(f"[Progress] finished task={task}")
