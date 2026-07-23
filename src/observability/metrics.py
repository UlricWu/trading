# filepath: src/observability/metrics.py
from __future__ import annotations

from dataclasses import dataclass, field

from src import logs


MetricValue = int | float | str | bool | None


@dataclass(slots=True)
class MetricRecorder:
    enabled: bool = True
    metrics: dict[str, MetricValue] = field(default_factory=dict)

    def record(self, name: str, value: MetricValue) -> None:
        if not self.enabled:
            return
        self.metrics[name] = value
        logs.info(f"[Metric] recorded name={name} value={value}")
