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
    """
    ledger: ExecutionLedger
    equity: EquityCurve

    def summary(self) -> dict[str, float]:
        submits = 0
        rejects = 0
        fills = 0
        turnover = 0.0

        for r in self.ledger.records:
            ev = r.get("event")
            if ev == "ORDER_SUBMIT":
                submits += 1
            elif ev == "ORDER_REJECT":
                rejects += 1
            elif ev == "FILL":
                fills += 1
                turnover += abs(float(r.get("qty", 0.0)) * float(r.get("price", 0.0)))

        equities = [float(p.equity) for p in self.equity.points]
        avg_eq = sum(equities) / len(equities) if equities else 0.0
        turnover_ratio = (turnover / avg_eq) if avg_eq > 0 else 0.0

        fill_rate = (fills / submits) if submits > 0 else 0.0
        reject_rate = (rejects / submits) if submits > 0 else 0.0

        return {
            "order_submits": float(submits),
            "order_rejects": float(rejects),
            "fills": float(fills),
            "fill_rate": float(fill_rate),
            "reject_rate": float(reject_rate),
            "turnover": float(turnover_ratio),
        }
