# filepath: src/trading/core/ledger.py
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from threading import Lock
from typing import Literal, NotRequired, Required, TypedDict

from src.trading.core.events import CancelOrders, FillEvent, OrderEvent, OrderIntent
from src.trading.portfolio.state import FillCost


class LedgerRecord(TypedDict, total=False):
    """One execution fact; exact prices and charges serialize as decimal strings.

    Example:
        record = LedgerRecord(event="ORDER_SUBMIT", ts_us=1, symbol="600000",
                              side="BUY", qty=100, order_id=1, meta={})
    """

    event: Required[Literal["ORDER_SUBMIT", "ORDER_REJECT", "FILL"]]
    ts_us: Required[int]
    symbol: Required[str]
    side: Required[str]
    qty: Required[int]
    order_id: Required[int]
    meta: Required[dict[str, object]]
    reason: NotRequired[str]
    price: NotRequired[str]
    fill_id: NotRequired[str]
    observation_id: NotRequired[str]
    reference_price: NotRequired[str]
    commission: NotRequired[str]
    stamp_tax: NotRequired[str]
    transfer_fee: NotRequired[str]


@dataclass(slots=True)
class ExecutionLedger:
    """
    Append-only execution fact ledger.

    Semantics:
    - run-scoped append-only execution log
    - stores ONLY execution facts (no inferred pnl)
    - owns monotonic order_id generator (run scoped)

    Record types:
    - ORDER_SUBMIT
    - ORDER_REJECT
    - FILL

    Extended:
    - reject reason statistics (run scoped)

    Example:
        ledger = ExecutionLedger()
        order_id = ledger.next_order_id()
    """

    records: list[LedgerRecord] = field(default_factory=list)
    commands: list[OrderIntent | CancelOrders] = field(default_factory=list)
    orders_by_id: dict[int, OrderIntent] = field(default_factory=dict)
    order_events: list[OrderEvent] = field(default_factory=list)
    fills_by_id: dict[str, FillEvent] = field(default_factory=dict)
    order_turnover: dict[int, Decimal] = field(default_factory=dict)
    order_costs: dict[int, FillCost] = field(default_factory=dict)

    # run-scoped monotonic id
    _order_seq: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    # NEW: reject reason counter
    _reject_reason_counter: Counter[str] = field(default_factory=Counter, repr=False)

    # ==============================================================
    # Order ID Generator
    # ==============================================================

    def next_order_id(self) -> int:
        """
        Returns:
            Monotonic increasing order_id within this run.
        """
        with self._lock:
            self._order_seq += 1
            return int(self._order_seq)

    # ==============================================================
    # Record APIs
    # ==============================================================

    def record_order_submit(
        self,
        *,
        ts_us: int,
        symbol: str,
        side: str,
        qty: int,
        order_id: int,
        meta: Mapping[str, object] | None = None,
    ) -> None:
        """Record the submitted quantity and advance the run's identity sequence.

        Example:
            ledger.record_order_submit(ts_us=1, symbol="600000", side="BUY",
                                       qty=100, order_id=1)
        """
        self._order_seq = max(self._order_seq, order_id)
        self.records.append(
            {
                "event": "ORDER_SUBMIT",
                "ts_us": int(ts_us),
                "symbol": str(symbol),
                "side": str(side),
                "qty": int(qty),
                "order_id": int(order_id),
                "meta": dict(meta or {}),
            }
        )

    def record_order_reject(
        self,
        *,
        ts_us: int,
        symbol: str,
        side: str,
        qty: int,
        order_id: int,
        reason: str,
        meta: Mapping[str, object] | None = None,
    ) -> None:
        r = (reason or "").strip() or "UNKNOWN"

        # --- NEW: count reject reason ---
        self._reject_reason_counter[r] += 1

        self.records.append(
            {
                "event": "ORDER_REJECT",
                "ts_us": int(ts_us),
                "symbol": str(symbol),
                "side": str(side),
                "qty": int(qty),
                "order_id": int(order_id),
                "reason": r,
                "meta": dict(meta or {}),
            }
        )

    def record_fill(
        self,
        *,
        fill: FillEvent,
        cost: FillCost,
        meta: Mapping[str, object],
    ) -> None:
        """Publish a confirmed fill and its identity and fee indexes together.

        Example:
            ledger.record_fill(fill=fill, cost=cost, meta=account_facts)
        """
        self.fills_by_id[fill.fill_id] = fill
        self.order_turnover[fill.order_id] = self.order_turnover.get(
            fill.order_id, Decimal("0")
        ) + fill.price * fill.qty
        prior = self.order_costs.get(
            fill.order_id, FillCost(Decimal("0"), Decimal("0"), Decimal("0"))
        )
        self.order_costs[fill.order_id] = FillCost(
            prior.commission + cost.commission,
            prior.stamp_tax + cost.stamp_tax,
            prior.transfer_fee + cost.transfer_fee,
        )
        self.records.append(
            {
                "event": "FILL",
                "ts_us": fill.ts_us,
                "symbol": fill.symbol,
                "side": fill.side.value,
                "qty": fill.qty,
                "price": str(fill.price),
                "order_id": fill.order_id,
                "fill_id": fill.fill_id,
                "observation_id": fill.observation_id,
                "reference_price": str(fill.reference_price),
                "commission": str(cost.commission),
                "stamp_tax": str(cost.stamp_tax),
                "transfer_fee": str(cost.transfer_fee),
                "meta": dict(meta),
            }
        )

    # ==============================================================
    # Reject Reason Statistics (NEW)
    # ==============================================================

    def reject_reason_total(self) -> int:
        """
        Returns total number of reject events.
        """
        return int(sum(self._reject_reason_counter.values()))

    def reject_reason_topn(self, n: int = 10) -> list[tuple[str, int]]:
        """
        Returns top-N reject reasons by count.
        """
        n = max(0, int(n))
        if n == 0:
            return []
        return list(self._reject_reason_counter.most_common(n))

    def format_reject_reason_topn(self, n: int = 10) -> str:
        """
        Human-readable summary for logging.
        """
        total = self.reject_reason_total()
        if total <= 0:
            return "none"

        parts: list[str] = []
        for reason, count in self.reject_reason_topn(n):
            pct = 100.0 * float(count) / float(total) if total > 0 else 0.0
            parts.append(f"{reason}:{count}({pct:.1f}%)")

        return ", ".join(parts)
