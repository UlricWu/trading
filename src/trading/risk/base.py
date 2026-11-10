# filepath: src/trading/risk/base.py
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Protocol

from pydantic import ConfigDict, PlainSerializer

from src.trading.core.events import PendingOrder

TargetPositions = Mapping[str, int]


@dataclass(frozen=True, slots=True)
class RiskContext:
    """An immutable valuation and order snapshot available at a risk check.

    Example:
        context = RiskContext(1, {"600000": 10.0}, 20000, 20000, 20000,
                              {"600000": 0}, (), {"600000": 100})
    """

    __pydantic_config__ = ConfigDict(extra="forbid")

    ts_us: int
    prices: Annotated[Mapping[str, float], PlainSerializer(lambda value: dict(value), return_type=dict[str, float])]
    equity: float
    cash: float
    peak_equity: float
    positions: Annotated[Mapping[str, int], PlainSerializer(lambda value: dict(value), return_type=dict[str, int])]
    pending_orders: tuple[PendingOrder, ...]
    quantity_steps: Annotated[Mapping[str, int], PlainSerializer(lambda value: dict(value), return_type=dict[str, int])]

    def __post_init__(self) -> None:
        object.__setattr__(self, "prices", MappingProxyType(dict(self.prices)))
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))
        object.__setattr__(self, "quantity_steps", MappingProxyType(dict(self.quantity_steps)))
        object.__setattr__(self, "pending_orders", tuple(self.pending_orders))
        if any(not math.isfinite(value) for value in (self.equity, self.cash, self.peak_equity)):
            raise ValueError("risk account values must be finite")
        if self.peak_equity < max(0, self.equity):
            raise ValueError("peak_equity must cover current equity")
        if any(not math.isfinite(price) or price <= 0 for price in self.prices.values()):
            raise ValueError("risk prices must be finite and positive")
        if any(type(qty) is not int or qty < 0 for qty in self.positions.values()):
            raise ValueError("risk positions must be non-negative integers")
        if any(type(step) is not int or step <= 0 for step in self.quantity_steps.values()):
            raise ValueError("risk quantity steps must be positive integers")


@dataclass(frozen=True, slots=True)
class RuleObservation:
    """Retain each rule's actual inputs and outputs, including untriggered rules.

    Example:
        observation = RuleObservation("max_single_name", {"600000": 1000},
                                      {"600000": 400}, True, "notional_cap")
    """

    rule: str
    before: Annotated[Mapping[str, int], PlainSerializer(lambda value: dict(value), return_type=dict[str, int])]
    after: Annotated[Mapping[str, int], PlainSerializer(lambda value: dict(value), return_type=dict[str, int])]
    triggered: bool
    reason: str
    checked: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "before", MappingProxyType(dict(self.before)))
        object.__setattr__(self, "after", MappingProxyType(dict(self.after)))


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Distinguish no submission from an explicit zero liquidation target.

    Example:
        decision = RiskDecision(adjusted=None, cancel_order_ids=(), reason="no_action", rules=())
    """

    adjusted: Annotated[Mapping[str, int], PlainSerializer(lambda value: dict(value), return_type=dict[str, int])] | None
    cancel_order_ids: tuple[int, ...]
    reason: str
    rules: tuple[RuleObservation, ...]

    def __post_init__(self) -> None:
        if self.adjusted is not None:
            object.__setattr__(self, "adjusted", MappingProxyType(dict(self.adjusted)))


class RiskRule(Protocol):
    """One pure risk calculation shared by fixed cases and feedback replay.

    Example:
        observation = rule.apply(target={"600000": 1000}, ctx=context)
    """

    def apply(self, *, target: TargetPositions, ctx: RiskContext) -> RuleObservation:
        """Observe a rule and return its complete target.

        Example:
            observation = rule.apply(target=target, ctx=context)
        """
        ...
