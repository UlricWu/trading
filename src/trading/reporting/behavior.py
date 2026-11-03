# filepath: src/trading/reporting/behavior.py
from __future__ import annotations

from dataclasses import dataclass

from src.trading.core.ledger import ExecutionLedger
from src.trading.core.equity import EquityCurve


@dataclass(frozen=True, slots=True)
class BehaviorReport:
    """
    Summarize execution behavior from ledger facts.

    Reads ONLY:
    - Ledger
    - EquityCurve (for avg equity turnover normalization)

    Example:
        report = BehaviorReport(ledger=ledger, equity=curve)
        metrics = report.summary()
    """
    ledger: ExecutionLedger
    equity: EquityCurve

    def summary(self) -> dict[str, float | None]:
        """Count completed order identities and filled quantities independently.

        Example:
            metrics = BehaviorReport(ledger=ledger, equity=curve).summary()
        """
        submitted: dict[int, int] = {}
        filled: dict[int, int] = {}
        rejects = 0
        fills = 0
        turnover = 0.0

        for r in self.ledger.records:
            ev = r.get("event")
            if ev == "ORDER_SUBMIT":
                submitted[r["order_id"]] = r["qty"]
            elif ev == "ORDER_REJECT":
                rejects += 1
            elif ev == "FILL":
                fills += 1
                order_id = r["order_id"]
                filled[order_id] = filled.get(order_id, 0) + r["qty"]
                turnover += abs(float(r.get("qty", 0.0)) * float(r.get("price", 0.0)))

        equities = [float(p.equity) for p in self.equity.points]
        avg_eq = sum(equities) / len(equities) if equities else 0.0
        turnover_ratio = (turnover / avg_eq) if avg_eq > 0 else 0.0

        submits = len(submitted)
        completed = sum(filled.get(order_id, 0) == qty for order_id, qty in submitted.items())
        requested_qty = sum(submitted.values())
        filled_qty = sum(filled.values())
        fill_rate = completed / submits if submits else None
        reject_rate = rejects / submits if submits else None

        return {
            "order_submits": float(submits),
            "order_rejects": float(rejects),
            "fills": float(fills),
            "fill_rate": fill_rate,
            "reject_rate": reject_rate,
            "completed_orders": float(completed),
            "requested_qty": float(requested_qty),
            "filled_qty": float(filled_qty),
            "qty_fill_rate": filled_qty / requested_qty if requested_qty else None,
            "turnover": float(turnover_ratio),
        }
