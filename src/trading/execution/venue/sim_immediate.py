# filepath: src/trading/execution/venue/sim_immediate.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.trading.core.events import FillEvent, OrderIntent
from src.trading.market.data_view import MarketDataView
from src.trading.execution.models.slippage_fixed_bp import FixedBPSlippageModel


@dataclass(frozen=True, slots=True)
class SimImmediateVenue:
    """
    Immediate-fill simulation venue.

    Semantics:
    - immediate fills at observable market price + slippage model
    - no partial fills, no queue, no impact

    Example:
        venue = SimImmediateVenue(FixedBPSlippageModel(bp=5))
        fills = venue.execute(ts_us=1, intents=orders, data_view=view, forced_close=False)
    """
    slippage_model: FixedBPSlippageModel

    def execute(
        self,
        *,
        ts_us: int,
        intents: Sequence[OrderIntent],
        data_view: MarketDataView,
        forced_close: bool,
    ) -> list[FillEvent]:
        """Produce identified fills for the existing synchronous daily caller.

        Example:
            fills = venue.execute(ts_us=1, intents=orders, data_view=view, forced_close=False)
        """
        fills: list[FillEvent] = []

        for it in intents:
            px = data_view.get_price(it.symbol)
            if px is None:
                continue

            exec_px = self.slippage_model.apply(side=it.side.value, price=float(px))
            fills.append(
                FillEvent(
                    ts_us=int(ts_us),
                    symbol=str(it.symbol),
                    side=it.side,
                    qty=int(it.qty),
                    price=float(exec_px),
                    order_id=int(it.order_id),
                    fill_id=f"immediate:{it.order_id}",
                    observation_id=f"immediate:{ts_us}:{it.symbol}",
                    reference_price=px,
                    forced_close=bool(forced_close),
                )
            )

        return fills
