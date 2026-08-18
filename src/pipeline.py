# filepath: src/pipeline.py
"""Minimal ordered Step execution shared by domain Pipelines."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.observability.instrumentation import Instrumentation


class PipelineStep[ContextT](Protocol):
    """Transform one domain Context within an ordered Step chain.

    Example:
        class NormalizeStep:
            def run(self, context: DataContext) -> DataContext:
                return context

        step: PipelineStep[DataContext] = NormalizeStep()
        next_context = step.run(DataContext(trade_date="2026-07-20"))
    """

    def run(self, context: ContextT) -> ContextT:
        """Execute this Step against the current Context.

        Example:
            next_context = step.run(context)
        """
        ...


def run_steps[ContextT](
    *,
    context: ContextT,
    steps: Sequence[PipelineStep[ContextT]],
    instrumentation: Instrumentation,
) -> ContextT:
    """Run Steps in the supplied order and measure every Step automatically.

    Example:
        result = run_steps(
            context=context,
            steps=(ingest_step, normalize_step),
            instrumentation=instrumentation,
        )
    """
    for step in steps:
        context = instrumentation.measure(
            type(step).__name__,
            step.run,
            context,
        )
    return context
