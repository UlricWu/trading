# filepath: src/data_system/steps/level2_minute_build.py
"""Publish sparse Level-2 stock minute facts from committed trade facts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from time import monotonic
from typing import Literal

import pyarrow as pa
import pyarrow.compute as pc

from src import logs
from src.access import Access, meta
from src.data_system.builders.level2_stock_trade_1m import (
    build_level2_stock_trade_1m,
)
from src.data_system.context import DataContext
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager

_PROGRESS_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class _MinuteFactPlan:
    exchange: Literal["sh", "sz"]
    input_dataset: str
    output_dataset: str


_MINUTE_FACT_PLANS = (
    _MinuteFactPlan(
        exchange="sh",
        input_dataset="sh_trade",
        output_dataset="sh_stock_trade_1m",
    ),
    _MinuteFactPlan(
        exchange="sz",
        input_dataset="sz_trade",
        output_dataset="sz_stock_trade_1m",
    ),
)


class Level2MinuteBuildStep:
    """Build both exchange-specific minute facts over resolved trade dates.

    Example:
        step = Level2MinuteBuildStep(
            pm=path_manager,
            access=access,
            processed_version="v1",
            symbol_batch_size=16,
        )
        step.run(
            DataContext(
                start="2026-07-27",
                end="2026-07-27",
                trade_dates=("2026-07-27",),
            )
        )
    """

    def __init__(
        self,
        *,
        pm: PathManager,
        access: Access,
        processed_version: str,
        symbol_batch_size: int,
    ) -> None:
        """Bind one formal store, Access identity, version, and batch bound.

        Example:
            step = Level2MinuteBuildStep(
                pm=path_manager,
                access=access,
                processed_version="v1",
                symbol_batch_size=16,
            )
        """
        if not isinstance(symbol_batch_size, int) or isinstance(
            symbol_batch_size,
            bool,
        ):
            raise TypeError("symbol_batch_size must be an int")
        if symbol_batch_size <= 0:
            raise ValueError("symbol_batch_size must be positive")
        self._pm = pm
        self._access = access
        self._processed_version = PathManager.require_safe_basename(
            processed_version,
            "processed_version",
        )
        self._symbol_batch_size = symbol_batch_size

    def run(self, context: DataContext) -> DataContext:
        """Build dates in ascending Context order and SH before SZ.

        Example:
            next_context = step.run(
                DataContext(
                    start="2026-07-27",
                    end="2026-07-27",
                    trade_dates=("2026-07-27",),
                )
            )
        """
        for trade_date in context.trade_dates:
            for plan in _MINUTE_FACT_PLANS:
                self._build_partition(trade_date=trade_date, plan=plan)
        return context

    def _build_partition(
        self,
        *,
        trade_date: str,
        plan: _MinuteFactPlan,
    ) -> None:
        output_paths = self._pm.processed_object(
            dataset_name=plan.output_dataset,
            version=self._processed_version,
            trade_date=trade_date,
        )
        input_paths = self._pm.processed_object(
            dataset_name=plan.input_dataset,
            version=self._processed_version,
            trade_date=trade_date,
        )
        expected_upstream_path = PurePosixPath(
            input_paths.meta_path.relative_to(self._pm.storage_root).as_posix()
        )
        existing = meta.find(
            pm=self._pm,
            meta_path=output_paths.meta_path,
            expected_payload_path=output_paths.payload_path,
        )
        if existing is not None:
            if (
                existing.upstream is None
                or existing.upstream[0] != expected_upstream_path
            ):
                raise RuntimeError(
                    f"Level-2 minute upstream mismatch: "
                    f"target={plan.output_dataset} trade_date={trade_date}"
                )
            if existing.symbol_slices is not None:
                raise RuntimeError(
                    f"Level-2 minute Meta must not contain symbol_slices: "
                    f"target={plan.output_dataset} trade_date={trade_date}"
                )
            logs.info(
                f"♻️ Level-2 minute fact; target={plan.output_dataset} "
                f"trade_date={trade_date} meta={output_paths.meta_path}"
            )
            return

        symbols = self._access.level2_symbols(
            trade_date=trade_date,
            exchange=plan.exchange,
        )
        started_at_seconds = monotonic()
        last_progress_at_seconds = started_at_seconds
        logs.info(
            f"▶️ Level-2 minute fact; target={plan.output_dataset} "
            f"trade_date={trade_date} symbols={len(symbols)}"
        )

        minute_batches: list[pa.Table] = []
        for batch_start in range(0, len(symbols), self._symbol_batch_size):
            batch_symbols = symbols[
                batch_start : batch_start + self._symbol_batch_size
            ]
            trades_by_symbol = self._access.trades(
                trade_date=trade_date,
                symbols=batch_symbols,
                exchange=plan.exchange,
            )
            minute_batches.append(
                build_level2_stock_trade_1m(
                    pa.concat_tables(list(trades_by_symbol.values())),
                    trade_date=trade_date,
                )
            )
            symbols_processed = batch_start + len(batch_symbols)
            now_seconds = monotonic()
            if (
                symbols_processed < len(symbols)
                and now_seconds - last_progress_at_seconds
                >= _PROGRESS_INTERVAL_SECONDS
            ):
                logs.info(
                    f"⏳ Level-2 minute fact; target={plan.output_dataset} "
                    f"trade_date={trade_date} "
                    f"symbols_processed={symbols_processed} "
                    f"symbols={len(symbols)} "
                    f"elapsed_seconds={int(now_seconds - started_at_seconds)}"
                )
                last_progress_at_seconds = now_seconds

        output = pa.concat_tables(minute_batches)
        write_parquet_atomic(output_file=output_paths.payload_path, table=output)
        meta.commit(
            pm=self._pm,
            payload_path=output_paths.payload_path,
            upstream_meta_path=input_paths.meta_path,
        )
        stock_ticks = pc.sum(output.column("trade_count")).as_py()
        logs.info(
            f"✅ Level-2 minute fact publish; target={plan.output_dataset} "
            f"trade_date={trade_date} stock_ticks={stock_ticks or 0} "
            f"rows={output.num_rows} "
            f"elapsed_seconds={monotonic() - started_at_seconds:.3f} "
            f"output={output_paths.payload_path}"
        )
