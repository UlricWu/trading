# filepath: src/pipeline.py
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from src.observability.instrumentation import Instrumentation


_ContextT = TypeVar("_ContextT")


def run_steps(
    context: _ContextT,
    steps: Sequence[Callable[[_ContextT], None]],
    instrumentation: Instrumentation,
) -> None:
    """Run concrete steps in order against one workflow context.

    Example:
        with Instrumentation("2026-07-20") as instrumentation:
            run_steps(context, (normalize_step,), instrumentation)
    """
    for step in steps:
        instrumentation.call(step, context)
