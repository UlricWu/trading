# filepath: src/trading/core/events.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class SignalEvent:
    """
    SignalEvent (FACT)
    - model output only (no trading semantics)
    """
    ts_us: int
    scores: Mapping[str, float]
    meta: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scores", MappingProxyType(dict(self.scores)))
        object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))


@dataclass(frozen=True, slots=True)
class TargetEvent:
    """
    TargetEvent (FACT)
    - ideal target positions (portfolio-construction output)
    """
    ts_us: int
    targets: Mapping[str, int]
    meta: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "targets", MappingProxyType(dict(self.targets)))
        object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """
    OrderIntent (FACT)
    - execution intent after clipping (institutional constraints)
    - still not a Fill
    """
    ts_us: int
    symbol: str
    side: Side
    qty: int
    order_id: int = 0
    meta: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))


@dataclass(frozen=True, slots=True)
class FillEvent:
    """
    FillEvent (FACT)
    - immutable execution result
    """
    ts_us: int
    symbol: str
    side: Side
    qty: int
    price: float
    order_id: int = 0
    forced_close: bool = False
