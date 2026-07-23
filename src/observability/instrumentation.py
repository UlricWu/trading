# filepath: src/observability/instrumentation.py
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from src import logs
from src.observability.context import InstrumentationContext
from src.observability.metrics import MetricRecorder
from src.observability.progress import ProgressReporter
from src.observability.timeline_reporter import TimelineReporter
from src.observability.timer import Timer


@dataclass(slots=True)
class Instrumentation:
    """Leaf-only runtime timing with explicit parent-scope suppression."""

    enabled: bool = True
    progress: ProgressReporter = field(init=False)
    metrics: MetricRecorder = field(init=False)
    context: InstrumentationContext = field(init=False)
    timeline: OrderedDict[str, float] = field(init=False)
    _timer: Timer = field(init=False)

    def __post_init__(self) -> None:
        self.progress = ProgressReporter(enabled=self.enabled)
        self._timer = Timer(enabled=self.enabled)
        self.metrics = MetricRecorder(enabled=self.enabled)
        self.context = InstrumentationContext()
        self.timeline = OrderedDict()

    @contextmanager
    def timer(self, name: str, *, record: bool = True) -> Iterator[None]:
        if not self.enabled:
            yield
            return

        self._timer.start(name)
        logs.info(f"[Timer] started name={name}")
        try:
            yield
        finally:
            elapsed_seconds = self._timer.end(name)
            if record:
                self.timeline[name] = (
                    self.timeline.get(name, 0.0) + elapsed_seconds
                )

    def generate_timeline_report(self, scope_name: str) -> None:
        TimelineReporter(self.timeline, scope_name).report()


class NoOpInstrumentation:
    """Instrumentation implementation with no observable side effects."""

    @contextmanager
    def timer(self, name: str, *, record: bool = True) -> Iterator[None]:
        yield

    def generate_timeline_report(self, scope_name: str) -> None:
        return None
