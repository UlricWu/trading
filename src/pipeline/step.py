# filepath: src/pipeline/step.py
from __future__ import annotations

from src.observability.instrumentation import (
    Instrumentation,
    NoOpInstrumentation,
)

from abc import ABC, abstractmethod
from typing import Generic, TypeVar


_ContextT = TypeVar("_ContextT")


class PipelineStep(ABC, Generic[_ContextT]):
    """
    Minimal orchestration step interface.

    The base class only supplies optional instrumentation capability and the
    executable `run(ctx)` contract. Workflow order and domain semantics belong
    to the concrete pipeline/workflow owner docs, not to this generic type.
    """

    def __init__(self, inst: Instrumentation | None = None) -> None:
        self.inst: Instrumentation | NoOpInstrumentation = (
            inst if inst is not None else NoOpInstrumentation()
        )

    @abstractmethod
    def run(self, ctx: _ContextT) -> _ContextT | None:
        """
        Execute this step and return the pipeline context.
        """
        raise NotImplementedError
