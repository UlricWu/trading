# filepath: src/trading/backtest/execution/models/cost_a_share.py
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.trading.portfolio.state import FillCost


@dataclass(frozen=True, slots=True)
class AShareCostModel:
    """Scenario fees on cumulative order notional, without currency rounding.

    Callers explicitly supply every rate. Actual market fee rounding is not inferred.

    Example:
        costs = AShareCostModel(0.0003, 0, 0, 5)
        fee = costs.compute(side="BUY", price=Decimal("10"), qty=200, symbol="600000")
    """

    commission_rate: Decimal | float
    stamp_tax_rate: Decimal | float
    transfer_rate: Decimal | float
    min_commission: Decimal | float

    def __post_init__(self) -> None:
        for value in (self.commission_rate, self.stamp_tax_rate, self.transfer_rate, self.min_commission):
            number = Decimal(str(value))
            if not number.is_finite() or number < 0:
                raise ValueError("fee parameters must be finite and non-negative")

    def compute(self, *, side: str, price: Decimal | float, qty: int, symbol: str) -> FillCost:
        """Compute charges for the supplied total executed notional.

        Example:
            fee = costs.compute(side="BUY", price=Decimal("10"), qty=200, symbol="600000")
        """
        turnover = Decimal(str(price)) * qty
        if turnover == 0:
            return FillCost(Decimal("0"), Decimal("0"), Decimal("0"))
        commission = max(turnover * Decimal(str(self.commission_rate)), Decimal(str(self.min_commission)))
        stamp = turnover * Decimal(str(self.stamp_tax_rate)) if side == "SELL" else Decimal("0")
        transfer = turnover * Decimal(str(self.transfer_rate)) if symbol.endswith(".SH") or symbol.startswith("6") else Decimal("0")
        return FillCost(commission, stamp, transfer)
