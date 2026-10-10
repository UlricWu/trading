# filepath: tests/trading/reporting/test_bundle.py
"""Readable completion logs from unchanged persisted backtest metrics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import logs
from src.trading.reporting.bundle import ReportingBundle
from src.utils.path import PathManager


@pytest.mark.parametrize("observation_count", [1, 200])
def test_report_summarizes_metrics_without_expanding_daily_details(
    tmp_path: Path, observation_count: int,
) -> None:
    pm = PathManager(tmp_path)
    experiment_name = "backtest_2026-08-10_2026-08-31_summary"
    metrics_path = pm.experiment_backtest_metrics(experiment_name=experiment_name)
    metrics_path.parent.mkdir()
    metrics = {
        "parameters": {"mode": "full_backtest", "start": "2026-08-10", "end": "2026-08-31"},
        "start_date": "2026-08-10", "end_date": "2026-08-28", "trade_days": 15,
        "final_equity": 200002.4561, "final_cash": 200002.4561, "final_positions": {},
        "total_return": 0.0000122805, "max_drawdown": 0.0000281535, "sharpe": 1.0214,
        "order_submits": 2.0, "completed_orders": 2.0, "order_rejects": 0.0,
        "filled_qty": 200.0, "requested_qty": 200.0, "qty_fill_rate": 1.0,
        "cost_total": 12.5439, "cost_commission": 10.0, "cost_tax": 1.2754, "cost_slippage": 1.2685,
        "model_evaluation": {
            "observations": [{"prediction_ref": "daily-detail-only.parquet"}] * observation_count,
            "summary": {"mean_ic": 0.06223456, "defined_date_count": 15, "date_count": 15},
        },
        "signal_eval": {
            "score_count": 79218, "mean_rank_ic": 0.06214567,
            "rank_ic_defined_date_count": 15, "frames": 15,
        },
        "tradable_alpha_eval": {"tradable_count": 79213, "sample_count": 79218, "tradable_coverage": 79213 / 79218},
        "risk_eval": {"changed_targets": 0, "blocked_count": 0, "scaled_count": 0},
    }
    metrics_path.write_text(json.dumps(metrics))
    original = metrics_path.read_bytes()
    output = tmp_path / "report.log"
    sink = logs.add(output, format="{message}", level="INFO", filter="src.trading.reporting.bundle")
    try:
        report_path = ReportingBundle(pm=pm).run(experiment_name=experiment_name)
    finally:
        logs.remove(sink)

    message = output.read_text()
    assert "期末权益 200,002.46 元" in message
    assert "区间收益 +0.0012%" in message
    assert "最大回撤 0.0028%" in message
    assert "完全成交 2 笔" in message
    assert "成交数量：200 / 200 股（100.00%）" in message
    assert "费用合计：12.54 元" in message
    assert "模型目标 Rank IC：0.062235（有效 15 / 15 日）" in message
    assert "次日原始收盘收益 Rank IC：0.062146（有效 15 / 15 日）" in message
    assert "有效评分 79,218 行" in message
    assert "79,213 / 79,218 行（99.99%）" in message
    assert str(metrics_path.parent) in message
    for name in ("predictions/", "execution.parquet", "equity.parquet", "metrics.json", "report.html"):
        assert name in message
    assert "daily-detail-only" not in message
    assert "parameters" not in message
    assert "backtest_metrics=" not in message
    assert len(message.splitlines()) < 25
    assert metrics_path.read_bytes() == original
    assert "daily-detail-only.parquet" in report_path.read_text()


def test_report_shows_undefined_metrics_and_no_orders_without_fabricating_zero(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    experiment_name = "backtest_2026-07-03_2026-07-06_no-orders"
    metrics_path = pm.experiment_backtest_metrics(experiment_name=experiment_name)
    metrics_path.parent.mkdir()
    metrics_path.write_text(json.dumps({
        "start_date": "2026-07-03", "end_date": "2026-07-03", "trade_days": 1,
        "final_equity": 200000.0, "final_cash": 200000.0, "final_positions": {},
        "total_return": 0.0, "max_drawdown": 0.0, "sharpe": None,
        "order_submits": 0.0, "completed_orders": 0.0, "order_rejects": 0.0,
        "filled_qty": 0.0, "requested_qty": 0.0, "qty_fill_rate": None,
        "cost_total": 0.0, "cost_commission": 0.0, "cost_tax": 0.0, "cost_slippage": 0.0,
        "model_evaluation": {"summary": {"mean_ic": None, "defined_date_count": 0, "date_count": 1}},
        "signal_eval": {"score_count": 0, "mean_rank_ic": None, "rank_ic_defined_date_count": 0, "frames": 1},
        "tradable_alpha_eval": {"tradable_count": 0, "sample_count": 0, "tradable_coverage": None},
        "risk_eval": {"changed_targets": 0, "blocked_count": 0, "scaled_count": 0},
    }))
    output = tmp_path / "report.log"
    sink = logs.add(output, format="{message}", level="INFO", filter="src.trading.reporting.bundle")
    try:
        ReportingBundle(pm=pm).run(experiment_name=experiment_name)
    finally:
        logs.remove(sink)

    message = output.read_text()
    assert "本次未提交订单" in message
    assert "成交数量：0 / 0 股（无值）" in message
    assert "夏普 无值" in message
    assert "模型目标 Rank IC：无值（有效 0 / 1 日）" in message
    assert "次日原始收盘收益 Rank IC：无值（有效 0 / 1 日）" in message
    assert "0 / 0 行（无值）" in message
