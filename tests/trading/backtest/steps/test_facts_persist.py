# filepath: tests/trading/backtest/steps/test_facts_persist.py
"""Execution identities, exact charges, and original valuation history on disk."""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import TypeAdapter

from src.trading.portfolio.state import PortfolioState
from src.trading.backtest.context import BacktestResultContext, BacktestState
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.facts_persist import FactsPersistStep
from src.trading.core.events import FillEvent, OrderEvent, OrderStatus, Side
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.risk import RiskCase, RiskCases, RiskContext, RiskManager, RiskObservation
from src.trading.risk.rules.max_position import MaxSingleNameNotionalRule
from src.utils.path import PathManager


@pytest.fixture
def context() -> BacktestResultContext:
    return BacktestResultContext(
        metrics=BacktestMetrics(mode='execution_eval', parameters={}, not_run={}),
        state=BacktestState(PortfolioState(20_000, 20_000)),
    )


def test_risk_cases_preserve_original_facts_without_reapplying_rules(
    tmp_path: Path, context: BacktestResultContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm = PathManager(tmp_path)
    case = RiskCase('risk-1', '2026-07-03', {'A': 1000},
        RiskContext(1, {'A': 10}, 20000, 20000, 20000, {}, (), {'A': 100}))
    decision = RiskManager((MaxSingleNameNotionalRule(.2),)).apply(target=case.target, ctx=case.context)
    observation = RiskObservation(case, decision)
    context.state.risk_observations.append(observation)
    expected = TypeAdapter(tuple[RiskCase, ...]).dump_json((case,), warnings='error')
    monkeypatch.setattr(RiskManager, 'apply', Mock(side_effect=AssertionError('must preserve original decisions')))

    FactsPersistStep(output_dir=pm.experiment_dir(experiment_name='risk-facts')).run(context)

    ref = pm.experiment_dir(experiment_name='risk-facts') / 'risk-cases.json'
    assert ref.read_bytes() == expected
    assert RiskCases.from_files((ref,)).cases == (case,)
    assert context.state.risk_observations == [observation]
    assert observation.decision is decision


def test_execution_preserves_identities_exact_money_metadata_and_order_states(
    tmp_path: Path, context: BacktestResultContext,
) -> None:
    pm = PathManager(tmp_path)
    ledger = context.state.execution_ledger
    ledger.record_order_submit(
        ts_us=1, symbol="600000", side="BUY", qty=100, order_id=7,
        meta={"symbol": "nested-value", "limits": {"max_qty": 100}},
    )
    price = Decimal("10.123456789123456789")
    fill = FillEvent(
        ts_us=2, symbol="600000", side=Side.BUY, qty=40, price=price, order_id=7,
        fill_id="7:minute-1", observation_id="minute-1", reference_price=Decimal("10"),
    )
    SettlementEngine(AShareCostModel(0.0003, 0, 0.00001, 0.2)).settle_fill(
        fill=fill, portfolio=context.state.portfolio_state, ledger=ledger,
    )
    ledger.record_order_submit(ts_us=3, symbol="000001", side="BUY", qty=100, order_id=8)
    ledger.record_order_reject(
        ts_us=3, symbol="000001", side="BUY", qty=100, order_id=8, reason="CASH",
    )
    ledger.order_events.extend((
        OrderEvent(1, 7, OrderStatus.PENDING, 100, "accepted", None, None),
        OrderEvent(2, 7, OrderStatus.PARTIAL, 60, "partial_fill", "minute-1", price),
        OrderEvent(3, 7, OrderStatus.EXPIRED, 60, "expired", None, None),
    ))
    original_records = deepcopy(ledger.records)
    original_events = tuple(ledger.order_events)
    original_cash = context.state.portfolio_state.cash

    assert FactsPersistStep(output_dir=pm.experiment_dir(experiment_name="execution")).run(context) is context

    table = pq.read_table(pm.experiment_backtest_execution(experiment_name="execution"))
    rows = table.to_pylist()
    records = [row for row in rows if row["source"] == "records"]
    assert [row["sequence"] for row in records] == [0, 1, 2, 3]
    assert [row["event"] for row in records] == ["ORDER_SUBMIT", "FILL", "ORDER_SUBMIT", "ORDER_REJECT"]
    assert records[0]["symbol"] == "600000"
    assert json.loads(records[0]["meta"]) == {"symbol": "nested-value", "limits": {"max_qty": 100}}
    assert records[0]["price"] is None
    assert records[0]["commission"] is None
    assert records[1]["order_id"] == 7
    assert records[1]["fill_id"] == "7:minute-1"
    assert records[1]["observation_id"] == "minute-1"
    assert records[1]["qty"] == 40
    assert records[1]["price"] == "10.123456789123456789"
    assert records[1]["reference_price"] == "10"
    assert Decimal(records[1]["commission"]) == Decimal("0.2")
    assert Decimal(records[1]["transfer_fee"]) == price * 40 * Decimal("0.00001")
    assert Decimal(records[1]["stamp_tax"]) == 0
    assert json.loads(records[1]["meta"]) == original_records[1]["meta"]
    assert records[3]["reason"] == "CASH"
    events = [row for row in rows if row["source"] == "order_events"]
    assert [row["sequence"] for row in events] == [0, 1, 2]
    assert [row["status"] for row in events] == ["pending", "partial", "expired"]
    assert [row["remaining_qty"] for row in events] == [100, 60, 60]
    assert events[1]["candidate_price"] == "10.123456789123456789"
    assert events[2]["candidate_price"] is None
    assert events[2]["symbol"] is None
    assert events[2]["event"] is None
    assert table.schema.field("order_id").type == pa.int64()
    assert table.schema.field("qty").type == pa.int64()
    assert table.schema.field("price").type == pa.string()
    assert ledger.records == original_records
    assert tuple(ledger.order_events) == original_events
    assert context.state.portfolio_state.cash == original_cash


def test_equity_keeps_actual_valuation_times_and_replaced_snapshot(
    tmp_path: Path, context: BacktestResultContext,
) -> None:
    pm = PathManager(tmp_path)
    curve = context.state.equity_curve
    curve.append(ts_us=1, cash=10_000.0, market_value=10_000.0)
    curve.append(ts_us=3, cash=9_000.0, market_value=7_000.0)
    curve.append(ts_us=3, cash=9_000.0, market_value=8_000.0)
    original_points = tuple(curve.points)

    FactsPersistStep(output_dir=pm.experiment_dir(experiment_name="equity")).run(context)

    table = pq.read_table(pm.experiment_backtest_equity(experiment_name="equity"))
    assert table.to_pylist() == [
        {"ts_us": 1, "cash": 10_000.0, "market_value": 10_000.0,
         "equity": 20_000.0, "peak_equity": 20_000.0, "drawdown": 0.0},
        {"ts_us": 3, "cash": 9_000.0, "market_value": 8_000.0,
         "equity": 17_000.0, "peak_equity": 20_000.0, "drawdown": -0.15},
    ]
    assert table.schema.field("ts_us").type == pa.int64()
    assert all(field.type == pa.float64() for field in list(table.schema)[1:])
    assert all(not field.nullable for field in table.schema)
    assert tuple(curve.points) == original_points


def test_empty_fact_collections_have_stable_typed_files(
    tmp_path: Path, context: BacktestResultContext,
) -> None:
    pm = PathManager(tmp_path)
    FactsPersistStep(output_dir=pm.experiment_dir(experiment_name="empty")).run(context)

    execution = pq.read_table(pm.experiment_backtest_execution(experiment_name="empty"))
    equity = pq.read_table(pm.experiment_backtest_equity(experiment_name="empty"))
    assert execution.num_rows == equity.num_rows == 0
    assert execution.schema.field("ts_us").type == pa.int64()
    assert execution.schema.field("qty").type == pa.int64()
    assert execution.schema.field("commission").type == pa.string()
    assert equity.column_names == ["ts_us", "cash", "market_value", "equity", "peak_equity", "drawdown"]
    assert equity.schema.field("cash").type == pa.float64()
