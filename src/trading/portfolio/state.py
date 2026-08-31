# filepath: src/trading/portfolio/state.py
from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field

from src.trading.core.events import FillEvent

FillKey = tuple[int, int, int, int, str, str]
# (order_id, ts_us, qty, price_i, side, symbol)


@dataclass(frozen=True, slots=True)
class FillCost:
    commission: float
    stamp_tax: float
    transfer_fee: float

    @property
    def total(self) -> float:
        return float(self.commission) + float(self.stamp_tax) + float(self.transfer_fee)


@dataclass(slots=True)
class PortfolioState:
    """
    Mutable portfolio state owned by one simulation session.

    Contract:
    - fact-only state driven ONLY by FillEvent (via settlement)
    - carries T+1 bookkeeping as FACTS (sellability), but does NOT enforce it

    Notes:
    - realized_pnl here is an explicit settlement fact (NET of costs)
    - reporting MUST NOT derive pnl from state; it reads ledger/tapes/equity only
    """
    initial_cash: float
    cash: float

    positions: dict[str, int] = field(default_factory=dict)
    avg_cost: dict[str, float] = field(default_factory=dict)

    realized_pnl: float = 0.0
    buy_qty_today: dict[str, int] = field(default_factory=dict)

    _applied_fills: set[FillKey] = field(default_factory=set, init=False)

    current_day: str | None = field(default=None, init=False)

    def on_run_start(self) -> None:
        self.current_day = None
        self.buy_qty_today.clear()
        self._applied_fills.clear()

    def on_trading_day_start(self, trade_date: str) -> None:
        self.current_day = str(trade_date)
        self.buy_qty_today.clear()

    def t1_sellable(self, symbol: str) -> int:
        pos = int(self.positions.get(symbol, 0))
        bought_today = int(self.buy_qty_today.get(symbol, 0))
        return max(0, pos - bought_today)

    def apply_fill(
        self,
        f: FillEvent,
        *,
        cost: FillCost | None,
        meta_out: MutableMapping[str, object],
    ) -> None:
        """
        Apply fill fact into portfolio state.

        This method:
        - updates cash/positions/avg_cost/realized_pnl
        - does NOT write ledger (ledger is written by settlement)
        - outputs explicit facts into meta_out (pnl, avg_cost, etc)
        """
        symbol = str(f.symbol)
        qty = int(f.qty)
        px = float(f.price)

        order_id = int(getattr(f, "order_id", 0) or 0)
        price_i = int(round(px * 1_000_000))
        key: FillKey = (order_id if order_id != 0 else -1, int(f.ts_us), qty, price_i, f.side.value, symbol)
        if key in self._applied_fills:
            meta_out["idempotent_skip"] = True
            return
        self._applied_fills.add(key)

        cur_pos = int(self.positions.get(symbol, 0))
        cur_avg = float(self.avg_cost.get(symbol, 0.0))

        meta_out.update(
            {
                "pos_before": cur_pos,
                "avg_cost_before": cur_avg,
            }
        )

        if f.side.value == "BUY":
            trade_val = px * qty
            self.cash -= trade_val

            new_pos = cur_pos + qty
            new_avg = ((cur_avg * cur_pos) + (px * qty)) / new_pos if new_pos > 0 else 0.0

            self.positions[symbol] = new_pos
            self.avg_cost[symbol] = new_avg
            self.buy_qty_today[symbol] = self.buy_qty_today.get(symbol, 0) + qty

            meta_out.update(
                {
                    "pos_after": new_pos,
                    "avg_cost_after": new_avg,
                }
            )

        elif f.side.value == "SELL":
            sell_qty = min(qty, cur_pos)
            if sell_qty <= 0:
                meta_out["reject_reason"] = "SELL_NO_POSITION"
                return

            proceeds = px * sell_qty
            self.cash += proceeds

            pnl_gross = (px - cur_avg) * sell_qty
            self.realized_pnl += pnl_gross

            new_pos = cur_pos - sell_qty
            if new_pos > 0:
                self.positions[symbol] = new_pos
            else:
                self.positions.pop(symbol, None)
                self.avg_cost.pop(symbol, None)
                self.buy_qty_today.pop(symbol, None)

            meta_out.update(
                {
                    "pos_after": new_pos,
                    "avg_cost": cur_avg,
                    "pnl_gross": float(pnl_gross),
                    "sell_qty": int(sell_qty),
                }
            )

            # costs are applied as explicit settlement facts
            if cost is not None:
                self.cash -= float(cost.total)
                self.realized_pnl -= float(cost.total)

                pnl_net = float(pnl_gross) - float(cost.total)
            else:
                pnl_net = float(pnl_gross)

            meta_out["pnl_net"] = float(pnl_net)

        else:
            raise ValueError(f"unknown side={f.side}")

        if cost is not None and f.side.value == "BUY":
            # apply cost for BUY as well (net-of-cost)
            self.cash -= float(cost.total)
            self.realized_pnl -= float(cost.total)

        meta_out["cash_after"] = float(self.cash)
        meta_out["realized_pnl_after"] = float(self.realized_pnl)
