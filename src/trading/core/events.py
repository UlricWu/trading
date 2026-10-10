# filepath: src/trading/core/events.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
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
    """A submitted quantity with an immutable execution window and quote.

    Example:
        order = OrderIntent(1, "600000", Side.BUY, 100, 1, 1, 3, Decimal("10"))
    """
    ts_us: int
    symbol: str
    side: Side
    qty: int
    order_id: int
    active_ts_us: int
    expires_ts_us: int
    limit_price: Decimal | None
    meta: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))
        if not self.ts_us <= self.active_ts_us < self.expires_ts_us:
            raise ValueError("order requires created <= active < expires")
        if type(self.qty) is not int or self.qty <= 0:
            raise ValueError("order qty must be a positive integer")
        if type(self.order_id) is not int or self.order_id <= 0:
            raise ValueError("order_id must be a positive integer")
        if self.limit_price is not None:
            price = Decimal(str(self.limit_price))
            if not price.is_finite() or price <= 0:
                raise ValueError("limit_price must be finite and positive")
            object.__setattr__(self, "limit_price", price)


@dataclass(frozen=True, slots=True)
class FillEvent:
    """A uniquely identified fill and its contemporaneous reference price.

    Example:
        fill = FillEvent(2, "600000", Side.BUY, 100, Decimal("10"), 1,
                         "1:minute-1", "minute-1", Decimal("10"))
    """
    ts_us: int
    symbol: str
    side: Side
    qty: int
    price: Decimal
    order_id: int
    fill_id: str
    observation_id: str
    reference_price: Decimal
    forced_close: bool = False

    def __post_init__(self) -> None:
        if not self.fill_id or not self.observation_id:
            raise ValueError("fill and observation identities must be nonempty")
        if type(self.qty) is not int or self.qty <= 0:
            raise ValueError("fill qty must be a positive integer")
        for name, raw_value in (("price", self.price), ("reference_price", self.reference_price)):
            value = Decimal(str(raw_value))
            if not value.is_finite() or value <= 0:
                raise ValueError(f"fill {name} must be finite and positive")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class OrderTerms:
    """The execution window supplied by a still-valid strategy plan.

    Example:
        terms = OrderTerms(active_ts_us=1, expires_ts_us=3)
    """

    active_ts_us: int
    expires_ts_us: int


@dataclass(frozen=True, slots=True)
class PendingOrder:
    """Immutable remaining quantity and resource reservation.

    Example:
        pending = PendingOrder(order, 100, Decimal("1000"), 0, Decimal("10"))
    """

    intent: OrderIntent
    remaining_qty: int
    reserved_cash: Decimal
    reserved_sell_qty: int
    reservation_price: Decimal


class OrderStatus(str, Enum):
    """Fake execution states.

    Example:
        status = OrderStatus.PARTIAL
    """

    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """An order transition or the reason it continued waiting.

    Example:
        event = OrderEvent(1, 1, OrderStatus.PENDING, 100, "accepted", None, None)
    """

    ts_us: int
    order_id: int
    status: OrderStatus
    remaining_qty: int
    reason: str
    observation_id: str | None
    candidate_price: Decimal | None


@dataclass(frozen=True, slots=True)
class CancelOrders:
    """An input command, distinct from the resulting order events.

    Example:
        command = CancelOrders(ts_us=3, order_ids=(1,))
    """

    ts_us: int
    order_ids: tuple[int, ...]
