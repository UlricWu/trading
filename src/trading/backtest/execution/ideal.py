# filepath: src/trading/backtest/execution/ideal.py
from __future__ import annotations

import math
from collections.abc import Mapping

from src.trading.core.events import FillEvent, Side
from src.trading.core.ledger import ExecutionLedger
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.market.data_view import MarketDataView
from src.trading.portfolio.state import PortfolioState


class IdealExecution:
    """Immediate zero-cost execution for the existing daily workflow.

    Example:
        execution = IdealExecution()
        fills = execution.execute_targets(ts_us=1, targets={"600000": 100},
            data_view=view, state=account, ledger=ledger)
    """

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
        """Settle the existing daily workflow's reference fills through settlement.

        Example:
            fills = execution.execute_targets(ts_us=1, targets={"600000": 100},
                data_view=view, state=account, ledger=ledger)
        """
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
                fill_id=f"ideal:{order_id}",
                observation_id=f"ideal:{ts_us}:{sym}",
                reference_price=price,
            )
            SettlementEngine(cost_model=None).settle_fill(fill=fill, portfolio=state, ledger=ledger)
            fills.append(fill)
        return fills
