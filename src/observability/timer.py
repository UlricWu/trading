# filepath: src/observability/timer.py
from __future__ import annotations

import time


class Timer:
    """Track elapsed monotonic seconds for explicitly named scopes."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._started_at: dict[str, float] = {}

    def start(self, name: str) -> None:
        if self.enabled:
            self._started_at[name] = time.perf_counter()

    def end(self, name: str) -> float:
        if not self.enabled or name not in self._started_at:
            return 0.0
        return time.perf_counter() - self._started_at.pop(name)
