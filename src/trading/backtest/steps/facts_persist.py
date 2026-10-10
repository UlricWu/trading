# filepath: src/trading/backtest/steps/facts_persist.py
"""Publish the execution and valuation facts used by backtest summaries."""

from __future__ import annotations

import json
from dataclasses import asdict

import pyarrow as pa
from pydantic import TypeAdapter

from src.trading.core.events import OrderIntent, CancelOrders, TargetEvent
from src.trading.risk.evaluation import RiskCase
from src.utils.filesystem import FileSystem

from src.trading.backtest.context import BacktestResultContext
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


_EXECUTION_SCHEMA = pa.schema([
    pa.field("source", pa.string(), nullable=False),
    pa.field("sequence", pa.int64(), nullable=False),
    pa.field("ts_us", pa.int64(), nullable=False),
    pa.field("order_id", pa.int64(), nullable=False),
    pa.field("event", pa.string()),
    pa.field("symbol", pa.string()),
    pa.field("side", pa.string()),
    pa.field("qty", pa.int64()),
    pa.field("reason", pa.string()),
    pa.field("price", pa.string()),
    pa.field("fill_id", pa.string()),
    pa.field("observation_id", pa.string()),
    pa.field("reference_price", pa.string()),
    pa.field("commission", pa.string()),
    pa.field("stamp_tax", pa.string()),
    pa.field("transfer_fee", pa.string()),
    pa.field("meta", pa.string()),
    pa.field("status", pa.string()),
    pa.field("remaining_qty", pa.int64()),
    pa.field("candidate_price", pa.string()),
])

_EQUITY_SCHEMA = pa.schema([
    pa.field("ts_us", pa.int64(), nullable=False),
    pa.field("cash", pa.float64(), nullable=False),
    pa.field("market_value", pa.float64(), nullable=False),
    pa.field("equity", pa.float64(), nullable=False),
    pa.field("peak_equity", pa.float64(), nullable=False),
    pa.field("drawdown", pa.float64(), nullable=False),
])


class FactsPersistStep:
    """Publish execution and equity files before derived metrics and the report.

    Example:
        step = FactsPersistStep(
            pm=path_manager, experiment_name="backtest_2026-07-01_2026-07-20_run-1",
        )
        persisted_context = step.run(completed_context)
    """

    def __init__(self, *, pm: PathManager, experiment_name: str) -> None:
        """Bind the destination of one backtest's original facts.

        Example:
            step = FactsPersistStep(
                pm=path_manager, experiment_name="backtest_2026-07-01_2026-07-20_run-1",
            )
        """
        self._pm = pm
        self._experiment_name = experiment_name

    def run(self, context: BacktestResultContext) -> BacktestResultContext:
        """Serialize existing facts without changing the runtime state.

        Example:
            persisted_context = step.run(completed_context)
        """
        ledger = context.state.execution_ledger
        rows = [
            {
                **record,
                "source": "records",
                "sequence": sequence,
                "meta": json.dumps(record["meta"], ensure_ascii=False, allow_nan=False),
            }
            for sequence, record in enumerate(ledger.records)
        ]
        rows.extend(
            {
                "source": "order_events",
                "sequence": sequence,
                "ts_us": event.ts_us,
                "order_id": event.order_id,
                "status": event.status.value,
                "remaining_qty": event.remaining_qty,
                "reason": event.reason,
                "observation_id": event.observation_id,
                "candidate_price": (
                    str(event.candidate_price) if event.candidate_price is not None else None
                ),
            }
            for sequence, event in enumerate(ledger.order_events)
        )
        write_parquet_atomic(
            output_file=self._pm.experiment_backtest_execution(experiment_name=self._experiment_name),
            table=pa.Table.from_pylist(rows, schema=_EXECUTION_SCHEMA),
        )
        write_parquet_atomic(
            output_file=self._pm.experiment_backtest_equity(experiment_name=self._experiment_name),
            table=pa.Table.from_pylist(
                [asdict(point) for point in context.state.equity_curve.points], schema=_EQUITY_SCHEMA,
            ),
        )
        run_dir = self._pm.experiment_dir(experiment_name=self._experiment_name)
        FileSystem.write_bytes_atomic(run_dir / "orders.json",
            TypeAdapter(tuple[OrderIntent | CancelOrders, ...]).dump_json(tuple(ledger.commands)))
        FileSystem.write_bytes_atomic(run_dir / "targets.json",
            TypeAdapter(tuple[TargetEvent, ...]).dump_json(tuple(context.state.target_tape.events)))
        if context.state.risk_observations:
            FileSystem.write_bytes_atomic(run_dir / "risk-cases.json",
                TypeAdapter(tuple[RiskCase, ...]).dump_json(
                    tuple(item.case for item in context.state.risk_observations)))
        return context
