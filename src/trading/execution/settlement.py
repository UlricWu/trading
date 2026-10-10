# filepath: src/trading/execution/settlement.py
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from src.trading.core.events import FillEvent, Side
from src.trading.core.ledger import ExecutionLedger
from src.trading.portfolio.state import FillCost, PortfolioState


class CostModel(Protocol):
    """A cumulative-notional fee calculation.

    Example:
        model: CostModel = AShareCostModel(0, 0, 0, 0)
        cost = model.compute(side="BUY", price=Decimal("10"), qty=100, symbol="600000")
    """

    def compute(self, *, side: str, price: Decimal | float, qty: int, symbol: str) -> FillCost:
        """Compute total fees for a notional.

        Example:
            cost = model.compute(side="BUY", price=Decimal("10"), qty=100, symbol="600000")
        """
        ...


@dataclass(frozen=True, slots=True)
class SettlementEngine:
    """Coordinate fill identity, cumulative fees, account and ledger once.

    Example:
        settlement = SettlementEngine(cost_model=costs)
        applied = settlement.settle_fill(fill=fill, portfolio=account, ledger=ledger)
    """

    cost_model: CostModel | None = None

    def incremental_cost(
        self, *, order_id: int, side: Side, symbol: str,
        turnover: Decimal, ledger: ExecutionLedger,
    ) -> FillCost:
        """Share cumulative charging between cash reservation and settlement.

        Example:
            cost = settlement.incremental_cost(order_id=1, side=Side.BUY,
                symbol="600000", turnover=Decimal("1000"), ledger=ledger)
        """
        zero = FillCost(Decimal("0"), Decimal("0"), Decimal("0"))
        if self.cost_model is None:
            return zero
        prior_turnover = ledger.order_turnover.get(order_id, Decimal("0"))
        prior = ledger.order_costs.get(order_id, zero)
        total = self.cost_model.compute(side=side.value, price=prior_turnover + turnover,
                                        qty=1, symbol=symbol)
        cost = FillCost(total.commission - prior.commission,
                        total.stamp_tax - prior.stamp_tax,
                        total.transfer_fee - prior.transfer_fee)
        if any(not charge.is_finite() or charge < 0 for charge in
               (cost.commission, cost.stamp_tax, cost.transfer_fee)):
            raise ValueError("incremental fees must be finite and non-negative")
        return cost

    def settle_fill(
        self, *, fill: FillEvent, portfolio: PortfolioState, ledger: ExecutionLedger,
    ) -> bool:
        """Return True once; reject conflicting identities before any mutation.

        Example:
            applied = settlement.settle_fill(fill=fill, portfolio=account, ledger=ledger)
        """
        prior = ledger.fills_by_id.get(fill.fill_id)
        if prior is not None:
            if prior != fill:
                raise ValueError(f"conflicting fill_id={fill.fill_id}")
            return False
        cost = self.incremental_cost(order_id=fill.order_id, side=fill.side,
            symbol=fill.symbol, turnover=fill.price * fill.qty, ledger=ledger)
        meta: dict[str, object] = {}
        portfolio.apply_fill(fill, cost=cost, meta_out=meta)
        ledger.record_fill(fill=fill, cost=cost, meta={
            **meta,
            "cost": {"commission": float(cost.commission), "stamp_tax": float(cost.stamp_tax),
                     "transfer_fee": float(cost.transfer_fee), "total": float(cost.total)},
            "slippage_cost": float(abs(fill.price - fill.reference_price) * fill.qty),
        })
        return True
