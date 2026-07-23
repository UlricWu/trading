# filepath: src/data_system/engines/orderbook_rebuild_engine.py
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math
from typing import Literal

import pyarrow as pa


@dataclass(slots=True)
class Order:
    order_id: int
    side: Literal["B", "S"]
    price: float
    volume: int
    ts_us: int


class OrderBook:
    """Maintain FIFO order identity and aggregate volume for each price level."""

    def __init__(self) -> None:
        self.orders: dict[int, Order] = {}
        self.bids: dict[float, deque[int]] = defaultdict(deque)
        self.asks: dict[float, deque[int]] = defaultdict(deque)
        self.bid_qty: dict[float, int] = defaultdict(int)
        self.ask_qty: dict[float, int] = defaultdict(int)
        self.last_ts_us: int | None = None

    def add_order(
        self,
        *,
        ts: int,
        order_id: int,
        side: str | None,
        price: float | None,
        volume: int | None,
    ) -> None:
        if side not in {"B", "S"} or price is None or volume is None:
            return
        if not math.isfinite(price) or price <= 0.0 or volume <= 0:
            return
        if order_id in self.orders:
            self.last_ts_us = ts
            return

        order = Order(
            order_id=order_id,
            side=side,
            price=float(price),
            volume=int(volume),
            ts_us=int(ts),
        )
        self.orders[order_id] = order
        if side == "B":
            self.bids[order.price].append(order_id)
            self.bid_qty[order.price] += order.volume
        else:
            self.asks[order.price].append(order_id)
            self.ask_qty[order.price] += order.volume
        self.last_ts_us = ts

    def cancel_order(self, *, ts: int, order_id: int) -> None:
        order = self.orders.pop(order_id, None)
        if order is None:
            self.last_ts_us = ts
            return

        if order.side == "B":
            self.bid_qty[order.price] -= order.volume
            if self.bid_qty[order.price] <= 0:
                self.bid_qty.pop(order.price, None)
                self.bids.pop(order.price, None)
        else:
            self.ask_qty[order.price] -= order.volume
            if self.ask_qty[order.price] <= 0:
                self.ask_qty.pop(order.price, None)
                self.asks.pop(order.price, None)
        self.last_ts_us = ts

    def trade(self, *, ts: int, order_id: int, volume: int | None) -> None:
        if volume is None or volume <= 0:
            self.last_ts_us = ts
            return
        order = self.orders.get(order_id)
        if order is None:
            self.last_ts_us = ts
            return

        filled = min(int(volume), order.volume)
        if order.side == "B":
            self.bid_qty[order.price] -= filled
        else:
            self.ask_qty[order.price] -= filled
        order.volume -= filled

        if order.volume == 0:
            self.orders.pop(order.order_id, None)
            if order.side == "B" and self.bid_qty.get(order.price, 0) == 0:
                self.bid_qty.pop(order.price, None)
                self.bids.pop(order.price, None)
            elif order.side == "S" and self.ask_qty.get(order.price, 0) == 0:
                self.ask_qty.pop(order.price, None)
                self.asks.pop(order.price, None)
        self.last_ts_us = ts

    def snapshot_table(self, depth: int = 10) -> pa.Table:
        """Return the top price levels without mutating book state."""
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
            raise ValueError("depth must be a non-negative integer")
        ts_us = self.last_ts_us if self.last_ts_us is not None else 0
        rows: list[tuple[int, str, int, float, int]] = []
        for level, price in enumerate(
            sorted(self.bid_qty, reverse=True)[:depth],
            start=1,
        ):
            rows.append((ts_us, "B", level, price, self.bid_qty[price]))
        for level, price in enumerate(sorted(self.ask_qty)[:depth], start=1):
            rows.append((ts_us, "S", level, price, self.ask_qty[price]))

        return pa.table(
            {
                "ts": pa.array([row[0] for row in rows], type=pa.int64()),
                "side": pa.array([row[1] for row in rows], type=pa.string()),
                "level": pa.array([row[2] for row in rows], type=pa.int16()),
                "price": pa.array([row[3] for row in rows], type=pa.float64()),
                "volume": pa.array([row[4] for row in rows], type=pa.int64()),
            }
        )
