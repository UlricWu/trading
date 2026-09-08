# filepath: src/trading/execution/models/cost_a_share.py
from __future__ import annotations

from dataclasses import dataclass

from src.trading.portfolio.state import FillCost


@dataclass(frozen=True, slots=True)
class AShareCostModel:
    """
    Compute A-share execution costs.

    Simplified production-usable cost model:
    - commission with minimum
    - stamp tax on SELL
    - transfer fee on SH (symbol hint)
    """
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    transfer_rate: float = 0.00001
    min_commission: float = 5.0

    def compute(self, *, side: str, price: float, qty: int, symbol: str) -> FillCost:
        turnover = float(price) * int(qty)

        commission = max(turnover * float(self.commission_rate), float(self.min_commission))
        stamp_tax = (turnover * float(self.stamp_tax_rate)) if side == "SELL" else 0.0
        transfer_fee = (turnover * float(self.transfer_rate)) if (symbol.endswith(".SH") or symbol.startswith("6")) else 0.0

        return FillCost(
            commission=float(commission),
            stamp_tax=float(stamp_tax),
            transfer_fee=float(transfer_fee),
        )
