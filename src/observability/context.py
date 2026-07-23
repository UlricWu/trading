# filepath: src/observability/context.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class InstrumentationContext:
    """Named runtime diagnostics that do not define workflow semantics."""

    state: dict[str, object] = field(default_factory=dict)

    def set(self, key: str, value: object) -> None:
        self.state[key] = value

    def get(self, key: str, default: object = None) -> object:
        return self.state.get(key, default)
