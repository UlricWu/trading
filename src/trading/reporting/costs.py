# filepath: src/trading/reporting/costs.py
from __future__ import annotations

from dataclasses import dataclass

from src.trading.core.ledger import ExecutionLedger


@dataclass(frozen=True, slots=True)
class CostReport:
    """
    Aggregate cost facts from the execution ledger.

    Reads ONLY:
    - ledger meta facts written by settlement
    """

    ledger: ExecutionLedger

    def summary(self) -> dict[str, float]:
        commission = 0.0
        tax = 0.0
        slippage = 0.0

        for r in self.ledger.records:
            if r.get("event") != "FILL":
                continue

            meta = r.get("meta", {}) or {}

            cost = meta.get("cost", {}) or {}

            commission += float(cost.get("commission", 0.0))
            tax += (
                float(cost.get("stamp_tax", 0.0))
                + float(cost.get("transfer_fee", 0.0))
            )

            # 👇 直接读取 settlement 写入的 slippage_cost
            slippage += float(meta.get("slippage_cost", 0.0))

        return {
            "cost_commission": float(commission),
            "cost_tax": float(tax),
            "cost_slippage": float(slippage),
            "cost_total": float(commission + tax + slippage),
        }
