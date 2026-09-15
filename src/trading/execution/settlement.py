# filepath: src/trading/execution/settlement.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.trading.core.events import FillEvent
from src.trading.core.ledger import ExecutionLedger
from src.trading.portfolio.state import PortfolioState, FillCost


class CostModel(Protocol):
    def compute(
        self,
        *,
        side: str,
        price: float,
        qty: int,
        symbol: str,
    ) -> FillCost: ...


@dataclass(frozen=True, slots=True)
class SettlementEngine:
    """
    Apply fills to portfolio state and the execution ledger.

    Responsibilities:
    - Apply fill to PortfolioState
    - Record ledger with full fact transparency
    - Compute slippage facts ONCE
    """

    cost_model: CostModel | None = None

    def settle_fill(
        self,
        *,
        fill: FillEvent,
        market_price: float,
        portfolio: PortfolioState,
        ledger: ExecutionLedger,
    ) -> None:

        exec_price = float(fill.price)
        qty = int(fill.qty)

        # --------------------------------------------
        # 1️⃣ Slippage facts (single source of truth)
        # --------------------------------------------
        slippage_amount = exec_price - float(market_price)

        # total slippage cost (absolute impact)
        slippage_cost = abs(slippage_amount * qty)

        # --------------------------------------------
        # 2️⃣ Trading cost model
        # --------------------------------------------
        cost: FillCost | None = None
        if self.cost_model is not None:
            cost = self.cost_model.compute(
                side=fill.side.value,
                price=exec_price,
                qty=qty,
                symbol=str(fill.symbol),
            )

        # --------------------------------------------
        # 3️⃣ Apply to portfolio state
        # --------------------------------------------
        meta_out: dict[str, object] = {}
        portfolio.apply_fill(fill, cost=cost, meta_out=meta_out)

        # --------------------------------------------
        # 4️⃣ Record ledger
        # --------------------------------------------
        ledger.record_fill(
            ts_us=int(fill.ts_us),
            symbol=str(fill.symbol),
            side=fill.side.value,
            qty=qty,
            price=exec_price,
            order_id=int(fill.order_id),
            meta={
                "cost": {
                    "commission": float(cost.commission) if cost else 0.0,
                    "stamp_tax": float(cost.stamp_tax) if cost else 0.0,
                    "transfer_fee": float(cost.transfer_fee) if cost else 0.0,
                    "total": float(cost.total) if cost else 0.0,
                },
                "slippage_cost": float(slippage_cost),
                **meta_out,
            },
        )
