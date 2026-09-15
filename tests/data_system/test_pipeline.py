# filepath: tests/data_system/test_pipeline.py
"""Behavior tests for the order-only offline data Pipeline."""

from __future__ import annotations

from typing import Protocol, Self, cast

from src.data_system.context import DataContext
from src.data_system.pipeline import DataPipeline
from src.observability.instrumentation import Instrumentation


class _Instrumentation:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def measure(
        self,
        operation_name: str,
        operation: _Operation,
        context: DataContext,
    ) -> DataContext:
        return operation(context)


class _Operation(Protocol):
    def __call__(self, context: DataContext) -> DataContext: ...


class _Step:
    def __init__(
        self,
        *,
        name: str,
        calls: list[str],
        result: DataContext,
    ) -> None:
        self._name = name
        self._calls = calls
        self._result = result

    def run(self, context: DataContext) -> DataContext:
        self._calls.append(self._name)
        return self._result


def test_data_pipeline_executes_each_step_once_in_supplied_order() -> None:
    calls: list[str] = []
    context = DataContext(start="2026-07-01", end="2026-07-20")
    pipeline = DataPipeline(
        steps=(
            _Step(name="third", calls=calls, result=context),
            _Step(name="first", calls=calls, result=context),
            _Step(name="second", calls=calls, result=context),
        ),
        instrumentation=cast("Instrumentation", _Instrumentation()),
    )

    result = pipeline.run(context)

    assert result is context
    assert calls == ["third", "first", "second"]
