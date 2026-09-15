# filepath: src/trading/execution/ideal.py
from __future__ import annotations

import math
from collections.abc import Mapping

from src.trading.core.events import FillEvent, Side
from src.trading.core.ledger import ExecutionLedger
from src.trading.market.data_view import MarketDataView
from src.trading.portfolio.state import PortfolioState


class IdealExecution:
    """Immediate zero-cost execution for model evaluation replay."""

    def execute_targets(
        self,
        *,
        ts_us: int,
        targets: Mapping[str, int],
        data_view: MarketDataView,
        state: PortfolioState,
        ledger: ExecutionLedger,
        forced_close: bool = False,
        ignore_t1_forced_close: bool = True,
    ) -> list[FillEvent]:
        fills: list[FillEvent] = []
        for symbol, target_qty in targets.items():
            sym = str(symbol)
            current_qty = int(state.positions.get(sym, 0))
            target_qty = int(target_qty)
            if target_qty < 0:
                raise RuntimeError(
                    f"[IdealExecution] target qty must be non-negative: "
                    f"symbol={sym} target={target_qty}"
                )

            delta = target_qty - current_qty
            if delta == 0:
                continue

            price = data_view.get_price(sym)
            if (
                price is None
                or not isinstance(price, (int, float))
                or not math.isfinite(float(price))
                or float(price) <= 0.0
            ):
                raise RuntimeError(
                    f"[IdealExecution] invalid raw close for symbol={sym}: "
                    f"price={price}"
                )

            side = Side.BUY if delta > 0 else Side.SELL
            qty = abs(int(delta))
            order_id = ledger.next_order_id()
            ledger.record_order_submit(
                ts_us=ts_us,
                symbol=sym,
                side=side.value,
                qty=qty,
                order_id=order_id,
            )

            fill = FillEvent(
                ts_us=int(ts_us),
                symbol=sym,
                side=side,
                qty=qty,
                price=float(price),
                order_id=order_id,
                forced_close=bool(forced_close),
            )
            meta_out: dict[str, object] = {}
            state.apply_fill(fill, cost=None, meta_out=meta_out)
            ledger.record_fill(
                ts_us=int(ts_us),
                symbol=sym,
                side=side.value,
                qty=qty,
                price=float(price),
                order_id=order_id,
                meta={
                    "cost": {
                        "commission": 0.0,
                        "stamp_tax": 0.0,
                        "transfer_fee": 0.0,
                        "total": 0.0,
                    },
                    "slippage_cost": 0.0,
                    **meta_out,
                },
            )
            fills.append(fill)
        return fills
