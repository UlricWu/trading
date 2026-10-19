# filepath: src/trading/backtest/steps/facts_persist.py
"""Publish the execution and valuation facts used by backtest summaries."""

from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import cast

import pyarrow as pa
from pydantic import TypeAdapter

from src.trading.core.events import OrderIntent, CancelOrders, TargetEvent
from src.trading.portfolio.corporate_actions import CorporateActionRecord
from src.trading.risk.evaluation import RiskCases
from src.utils.filesystem import FileSystem

from src.trading.backtest.context import BacktestContext, BacktestState
from src.utils.parquet_writer import write_parquet_atomic


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
    pa.field("dividend_receivable", pa.float64(), nullable=False),
])

_VALUATION_MARK_SCHEMA = pa.schema([
    pa.field("ts_us", pa.int64(), nullable=False),
    pa.field("trade_date", pa.string(), nullable=False),
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("qty", pa.int64(), nullable=False),
    pa.field("price", pa.string(), nullable=False),
    pa.field("price_ts_us", pa.int64(), nullable=False),
    pa.field("price_trade_date", pa.string(), nullable=False),
    pa.field("price_kind", pa.string(), nullable=False),
    pa.field("reason", pa.string(), nullable=False),
])


class FactsPersistStep:
    """Publish execution and equity files before derived metrics and the report.

    Example:
        step = FactsPersistStep(
            output_dir=Path("/data/experiments/backtest_2026-10-11_run"),
        )
        persisted_context = step.run(completed_context)
    """

    def __init__(self, *, output_dir: Path) -> None:
        """Bind the destination of one backtest's original facts.

        Example:
            step = FactsPersistStep(
                output_dir=Path("/data/experiments/backtest_2026-10-11_run"),
            )
        """
        self._output_dir = output_dir

    def run(self, context: BacktestContext) -> BacktestContext:
        """Serialize existing facts without changing the runtime state.

        Example:
            persisted_context = step.run(completed_context)
        """
        state = cast(BacktestState, context.state)
        ledger = state.execution_ledger
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
            output_file=self._output_dir / "execution.parquet",
            table=pa.Table.from_pylist(rows, schema=_EXECUTION_SCHEMA),
        )
        write_parquet_atomic(
            output_file=self._output_dir / "equity.parquet",
            table=pa.Table.from_pylist(
                [asdict(point) for point in state.equity_curve.points], schema=_EQUITY_SCHEMA,
            ),
        )
        write_parquet_atomic(
            output_file=self._output_dir / "valuation-marks.parquet",
            table=pa.Table.from_pylist(
                [{**asdict(mark), "price": str(mark.price)} for marks in state.valuation_marks.values() for mark in marks],
                schema=_VALUATION_MARK_SCHEMA,
            ),
        )
        run_dir = self._output_dir
        statuses = {day: [status.model_dump(mode="json") for status in reviews]
            for day, reviews in context.security_status_by_date.items()}
        FileSystem.write_bytes_atomic(run_dir / "security-status.json", json.dumps({
            "version": "v1", "statuses": [status for reviews in statuses.values() for status in reviews],
            "input_digests": {day: sha256(json.dumps(reviews, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest() for day, reviews in statuses.items()},
        }, ensure_ascii=False, indent=2, allow_nan=False).encode())
        actions = context.corporate_actions
        action_facts = {
            "tax_basis": "gross",
            "coverage": "distributions_and_supplied_notices",
            "input_digests": actions.input_digests,
            "distributions": [action.model_dump(mode="json") for action in actions.distributions],
            "revisions": [action.model_dump(mode="json") for action in actions.revisions],
            "source_resolutions": [item.model_dump(mode="json") for item in actions.resolutions],
            "notices": [notice.model_dump(mode="json") for notice in actions.notices],
            "records": TypeAdapter(list[CorporateActionRecord]).dump_python(state.corporate_action_records, mode="json"),
            "unresolved": [issue.model_dump(mode="json") for issue in state.corporate_action_issues.values()],
            "outstanding_entitlements": [item.model_dump(mode="json") for item in state.portfolio_state.distribution_entitlements.values()],
        }
        FileSystem.write_bytes_atomic(run_dir / "corporate-actions.json",
            json.dumps(action_facts, ensure_ascii=False, allow_nan=False, indent=2).encode())
        FileSystem.write_bytes_atomic(run_dir / "orders.json",
            TypeAdapter(tuple[OrderIntent | CancelOrders, ...]).dump_json(tuple(ledger.commands)))
        FileSystem.write_bytes_atomic(run_dir / "targets.json",
            TypeAdapter(tuple[TargetEvent, ...]).dump_json(tuple(state.target_tape.events)))
        if state.risk_observations:
            RiskCases(tuple(item.case for item in state.risk_observations)).write(run_dir / "risk-cases.json")
        return context
