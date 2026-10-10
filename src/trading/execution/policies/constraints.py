# filepath: src/trading/execution/policies/constraints.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from pydantic import ConfigDict, StrictBool, StrictInt

from src.trading.core.events import OrderIntent, OrderTerms, PendingOrder, Side, TargetEvent


@dataclass(frozen=True, slots=True)
class QuantityRules:
    """Explicit per-security scenario order/fill quantities and quote step.

    Example:
        rules = QuantityRules(100, 100, 1, 1, 1, Decimal("0.01"), True)
    """

    __pydantic_config__ = ConfigDict(extra="forbid")

    buy_min_qty: StrictInt
    buy_step_qty: StrictInt
    sell_min_qty: StrictInt
    sell_step_qty: StrictInt
    fill_step_qty: StrictInt
    price_tick: Decimal
    enforce_t1: StrictBool

    def __post_init__(self) -> None:
        if any(type(qty) is not int or qty <= 0 for qty in (
            self.buy_min_qty, self.buy_step_qty, self.sell_min_qty,
            self.sell_step_qty, self.fill_step_qty,
        )):
            raise ValueError("quantity minima and steps must be positive integers")
        if not self.price_tick.is_finite() or self.price_tick <= 0:
            raise ValueError("price_tick must be finite and positive")
        if type(self.enforce_t1) is not bool:
            raise ValueError("enforce_t1 must be explicit bool")


@dataclass(frozen=True, slots=True)
class QuoteRule:
    """Form quotes from currently visible raw prices after direction is known.

    Example:
        quote = QuoteRule(Decimal("0"), Decimal("0"))
        price = quote.price(side=Side.SELL, reference=Decimal("10"), tick=Decimal("0.01"))
    """

    __pydantic_config__ = ConfigDict(extra="forbid")

    buy_offset_bp: Decimal
    sell_offset_bp: Decimal

    def __post_init__(self) -> None:
        if any(not value.is_finite() or not 0 <= value < 10_000 for value in
               (self.buy_offset_bp, self.sell_offset_bp)):
            raise ValueError("quote offsets must be finite and in [0, 10000)")

    def price(self, *, side: Side, reference: Decimal, tick: Decimal) -> Decimal:
        """Round inside the declared buy upper or sell lower boundary.

        Example:
            price = quote.price(side=Side.BUY, reference=Decimal("10"), tick=Decimal("0.01"))
        """
        offset = self.buy_offset_bp if side == Side.BUY else self.sell_offset_bp
        boundary = reference * (1 + offset / 10_000 if side == Side.BUY else 1 - offset / 10_000)
        rounding = ROUND_FLOOR if side == Side.BUY else ROUND_CEILING
        return (boundary / tick).to_integral_value(rounding=rounding) * tick


@dataclass(frozen=True, slots=True)
class TargetOrderPlan:
    """Reconciliation computed before cancellation destroys inherited terms.

    Example:
        plan = TargetOrderPlan(cancel_order_ids=(1,), orders=(replacement,))
    """

    cancel_order_ids: tuple[int, ...]
    orders: tuple[OrderIntent, ...]


def reconcile_target_orders(
    *, target: TargetEvent, positions: Mapping[str, int], pending: Sequence[PendingOrder],
    cancel_order_ids: Sequence[int], terms: Mapping[str, OrderTerms],
    prices: Mapping[str, Decimal], rules: Mapping[str, QuantityRules],
    quote: QuoteRule | None, next_order_id: Callable[[], int],
) -> TargetOrderPlan:
    """Keep covered targets, cancel conflicts, and inherit each reduced order's terms.

    None quote declares reference execution; it is not a missing price-model fallback.

    Example:
        plan = reconcile_target_orders(target=target, positions=account.positions,
            pending=tuple(pending_orders.values()), cancel_order_ids=(), terms=terms,
            prices=prices, rules=rules, quote=quote, next_order_id=ledger.next_order_id)
    """
    controlled = set(positions) | {item.intent.symbol for item in pending}
    if not controlled <= target.targets.keys():
        raise ValueError("target must explicitly cover positions and pending symbols")
    if any(type(qty) is not int or qty < 0 for qty in target.targets.values()):
        raise ValueError("target quantities must be non-negative integers")
    cancellations = list(dict.fromkeys(cancel_order_ids))
    orders: list[OrderIntent] = []
    ordered = sorted(pending, key=lambda item: item.intent.active_ts_us)
    for symbol, target_qty in target.targets.items():
        delta = target_qty - positions.get(symbol, 0)
        side = Side.BUY if delta > 0 else Side.SELL
        needed = abs(delta)
        for item in (item for item in ordered if item.intent.symbol == symbol):
            old = item.intent
            keep = min(item.remaining_qty, needed) if old.side == side else 0
            if keep == item.remaining_qty and old.order_id not in cancellations:
                needed -= keep
                continue
            if old.order_id in cancel_order_ids and keep == item.remaining_qty:
                raise ValueError("cancel request conflicts with unchanged target commitment")
            if old.order_id not in cancellations:
                cancellations.append(old.order_id)
            if keep:
                orders.append(OrderIntent(target.ts_us, symbol, side, keep, next_order_id(),
                    max(target.ts_us, old.active_ts_us), old.expires_ts_us, old.limit_price))
                needed -= keep
        if needed:
            window = terms[symbol]
            limit = None if quote is None else quote.price(
                side=side, reference=prices[symbol], tick=rules[symbol].price_tick,
            )
            orders.append(OrderIntent(target.ts_us, symbol, side, needed, next_order_id(),
                window.active_ts_us, window.expires_ts_us, limit))
    return TargetOrderPlan(tuple(cancellations), tuple(orders))
