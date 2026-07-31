# filepath: src/data_system/engines/trade_enrich_engine.py
"""Append normalized trade derived fields to symbol-local Arrow tables."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc

from src.utils import table_ops
from src.data_system.arrow.ops import append_or_replace


class TradeEnrichEngine:
    """Append notional and tick-rule side to one symbol-local table.

    Example:
        engine = TradeEnrichEngine()
        enriched = engine.execute(
            pa.table({"price": [10.0], "volume": [100]})
        )
    """

    def __init__(
        self,
        *,
        price_col: str = "price",
        volume_col: str = "volume",
        notional_col: str = "notional",
        side_col: str = "trade_side",
    ) -> None:
        """Bind source and output column names.

        Example:
            engine = TradeEnrichEngine(
                price_col="price",
                volume_col="volume",
            )
        """
        self.price_col = price_col
        self.volume_col = volume_col
        self.notional_col = notional_col
        self.side_col = side_col

    # ==========================================================
    # Public API
    # ==========================================================
    def execute(self, table: pa.Table) -> pa.Table:
        """Return the enriched table without changing its row order.

        Example:
            enriched = TradeEnrichEngine().execute(
                pa.table({"price": [10.0], "volume": [100]})
            )
        """
        if table.num_rows == 0:
            return table

        table_ops.require_columns(
            table,
            (self.price_col, self.volume_col),
            who="TradeEnrichEngine",
        )

        price = table[self.price_col]
        volume = table[self.volume_col]

        notional = pc.multiply(
            pc.cast(price, pa.float64()),
            pc.cast(volume, pa.float64()),
        )

        trade_side = self._infer_trade_side(price)
        out = table
        out = append_or_replace(out, self.notional_col, notional)
        out = append_or_replace(out, self.side_col, trade_side)
        return out

    def _infer_trade_side(self, price: pa.ChunkedArray | pa.Array) -> pa.Array:
        """
        Tick rule：
          price[i] > price[i-1] -> +1
          price[i] < price[i-1] -> -1
          else -> 0

        说明：
          - 第 0 行 prev_price 为 null -> diff 为 null -> side=0
        """
        if isinstance(price, pa.ChunkedArray):
            price = price.combine_chunks()

        n = len(price)
        if n == 0:
            return pa.array([], type=pa.int8())

        null_head = pa.array([None], type=price.type)
        prev_tail = price.slice(0, n - 1)
        prev_price = pa.concat_arrays([null_head, prev_tail])

        diff = pc.subtract(
            pc.cast(price, pa.float64()),
            pc.cast(prev_price, pa.float64()),
        )

        buy = pc.greater(diff, 0)
        sell = pc.less(diff, 0)

        side = pc.if_else(
            buy,
            pa.scalar(1, pa.int8()),
            pc.if_else(
                sell,
                pa.scalar(-1, pa.int8()),
                pa.scalar(0, pa.int8()),
            ),
        )
        side = pc.fill_null(side, pa.scalar(0, pa.int8()))
        return pc.cast(side, pa.int8())
