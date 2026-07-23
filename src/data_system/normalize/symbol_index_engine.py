# filepath: src/data_system/normalize/symbol_index_engine.py
"""Build object-level canonical sort order and symbol slice indexes."""

from __future__ import annotations

from time import perf_counter

import pyarrow as pa
import pyarrow.compute as pc

from src import logs
from src.data_system.arrow.ops import sort_by


class SymbolIndexEngine:
    """Sort a complete Arrow table and build half-open symbol slices.

    The engine requires the full object-level table and performs no I/O; it
    must not be invoked independently for batches that share a symbol index.
    """

    # --------------------------------------------------
    @staticmethod
    def execute(
        table: pa.Table,
    ) -> tuple[pa.Table, dict[str, range]]:
        """Return the `(symbol, ts_utc)` sorted table and contiguous slices."""

        if table.num_rows == 0:
            return table, {}

        # --------------------------------------------------
        # 0) normalize symbol column type (CRITICAL)
        # --------------------------------------------------
        if "symbol" not in table.column_names:
            raise KeyError("[SymbolIndexEngine] missing 'symbol' column")
        if "ts_utc" not in table.column_names:
            raise KeyError("[SymbolIndexEngine] missing 'ts_utc' column")

        sym = table["symbol"]
        if pa.types.is_dictionary(sym.type):
            table = table.set_column(
                table.column_names.index("symbol"),
                "symbol",
                pc.cast(sym, pa.string()),
            )
        elif not pa.types.is_string(sym.type):
            raise TypeError(f"[SymbolIndexEngine] invalid symbol type: {sym.type}")

        ts = table["ts_utc"]
        if not (pa.types.is_integer(ts.type) or pa.types.is_timestamp(ts.type)):
            raise TypeError(f"[SymbolIndexEngine] invalid ts_utc type: {ts.type}")

        # --------------------------------------------------
        # 1) global sort（明确且显式）
        # --------------------------------------------------
        rows = table.num_rows
        sort_started_at = perf_counter()
        logs.info(f"[SymbolIndex] sort start rows={rows}")
        table = sort_by(table, ("symbol", "ts_utc"))
        logs.info(
            f"[SymbolIndex] sort done rows={rows} "
            f"took={perf_counter() - sort_started_at:.3f}s"
        )

        # --------------------------------------------------
        # 2) build symbol slice index
        # --------------------------------------------------
        index_started_at = perf_counter()
        sym = table["symbol"]

        if pa.types.is_dictionary(sym.type):
            sym = pc.cast(sym, pa.string())
        elif not pa.types.is_string(sym.type):
            raise TypeError(f"[SymbolIndexEngine] invalid symbol type: {sym.type}")

        ree = pc.run_end_encode(sym).combine_chunks()
        run_ends = ree.run_ends.to_pylist()
        values = ree.values.to_pylist()

        index: dict[str, range] = {}
        start = 0

        for symbol, end_exclusive in zip(values, run_ends):
            if not isinstance(symbol, str) or not symbol:
                raise ValueError(
                    "[SymbolIndexEngine] symbol must be a non-empty string"
                )
            end_exclusive = int(end_exclusive)
            index[symbol] = range(start, end_exclusive)
            start = end_exclusive

        logs.info(
            f"[SymbolIndex] index done symbols={len(index)} "
            f"took={perf_counter() - index_started_at:.3f}s"
        )
        return table, index
