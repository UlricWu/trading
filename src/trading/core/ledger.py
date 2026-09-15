# filepath: src/trading/core/ledger.py
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal, NotRequired, Required, TypedDict


class LedgerRecord(TypedDict, total=False):
    """One append-only execution fact in serialization-ready form."""

    event: Required[Literal["ORDER_SUBMIT", "ORDER_REJECT", "FILL"]]
    ts_us: Required[int]
    symbol: Required[str]
    side: Required[str]
    qty: Required[int]
    order_id: Required[int]
    meta: Required[dict[str, object]]
    reason: NotRequired[str]
    price: NotRequired[float]


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
    """

    records: list[LedgerRecord] = field(default_factory=list)

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
        ts_us: int,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        order_id: int,
        meta: Mapping[str, object] | None = None,
    ) -> None:
        self.records.append(
            {
                "event": "FILL",
                "ts_us": int(ts_us),
                "symbol": str(symbol),
                "side": str(side),
                "qty": int(qty),
                "price": float(price),
                "order_id": int(order_id),
                "meta": dict(meta or {}),
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
