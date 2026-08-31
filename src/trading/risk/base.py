# filepath: src/trading/risk/base.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

TargetPositions = Mapping[str, float]


@dataclass(frozen=True, slots=True)
class RiskContext:
    """
    Facts available to risk layer (no future info).

    - ts_us: current replay timestamp
    - prices: current mark prices (symbol -> price)
    - equity: current total equity
    - cash: current cash
    - peak_equity: historical peak equity (for drawdown)
    - positions  👈 新增
    - meta: optional facts (e.g. frequency)
    """
    ts_us: int
    prices: Mapping[str, float]
    equity: float
    cash: float
    peak_equity: float
    positions: Mapping[str, int]
    meta: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "prices", MappingProxyType(dict(self.prices)))
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))
        if self.meta is not None:
            object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """
    Immutable risk-decision result.

    adjusted: 最终目标仓位（可能被修改）
    blocked: 是否被完全拦截（清仓或拒绝）
    scaled: 是否发生了“仓位调整”（缩放/裁剪）
    reason: 命中规则说明
    """
    adjusted: Mapping[str, int]
    blocked: bool
    scaled: bool
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "adjusted", MappingProxyType(dict(self.adjusted)))


class RiskRule(Protocol):
    """
    RiskRule must be pure and deterministic:
        same (target, ctx) -> same output

    It must NOT:
    - generate alpha
    - use future data
    - call execution
    """

    name: str

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RiskDecision:
        ...
