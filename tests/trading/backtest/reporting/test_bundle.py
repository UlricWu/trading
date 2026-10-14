# filepath: tests/trading/backtest/reporting/test_bundle.py
import json
from pathlib import Path

import pytest

from src import logs
from src.trading.backtest.evaluation import SignalEvaluation, evaluate_execution, evaluate_portfolio
from src.trading.backtest.reporting.bundle import ReportingBundle
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.core.equity import EquityCurve
from src.trading.core.ledger import ExecutionLedger
from src.training.evaluation import ICObservation
from src.utils.path import PathManager


@pytest.mark.parametrize('count', [1, 200])
def test_report_summarizes_without_expanding_observations_and_preserves_metrics(tmp_path: Path, count: int) -> None:
    pm = PathManager(tmp_path)
    observations = tuple(ICObservation(model_ref='/model', decision_ts_us=ts,
        input_count=2, no_prediction_count=0, missing_label_count=0, value=.06223456, pair_count=2,
        undefined_reasons=(), prediction_ref='/daily-detail-only.parquet') for ts in range(count))
    metrics = BacktestMetrics(mode='signal_eval', parameters={'mode': 'signal_eval'},
        not_run={'risk': 'signal_only'}, signal_eval=SignalEvaluation(observations=observations,
            decision_count=count, trade_day_count=1, defined_day_count=1, mean_ic=.06223456,
            daily_ic={'2026-07-03': .06223456}, undefined_reason_counts={}))
    ref = pm.experiment_backtest_metrics(experiment_name='summary')
    ref.parent.mkdir(parents=True)
    ref.write_text(metrics.model_dump_json())
    original = ref.read_bytes()
    messages = []
    sink = logs.add(lambda message: messages.append(str(message)), format='{message}',
                    filter='src.trading.backtest.reporting.bundle')
    try:
        report = ReportingBundle(pm.experiment_dir(experiment_name='summary')).run()
    finally:
        logs.remove(sink)
    text = ''.join(messages)
    assert f'模型目标 Rank IC：0.062235（有效 1 / 1 日；{count} 次决策）' in text
    assert '未运行能力' in text and 'risk' in text
    assert 'daily-detail-only' not in text and 'parameters' not in text
    assert len(text.splitlines()) < 12
    assert ref.read_bytes() == original
    assert 'href="metrics.json"' in report.read_text()
    assert '成交评价' not in report.read_text()


def test_report_distinguishes_undefined_values_no_orders_and_unrun_capabilities(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    curve = EquityCurve()
    curve.append(ts_us=1, cash=100, market_value=0)
    curve.append(ts_us=2, cash=100, market_value=0)
    metrics = BacktestMetrics(mode='full_backtest', parameters={}, not_run={'signal': 'no_selected_records'},
        execution_eval=evaluate_execution(ExecutionLedger()), portfolio=evaluate_portfolio(curve,
            initial_equity=100, daily_equity={'2026-07-03': curve.points[-1]}, ledger=ExecutionLedger(), annual_factor=252))
    ref = pm.experiment_backtest_metrics(experiment_name='empty')
    ref.parent.mkdir(parents=True)
    ref.write_text(metrics.model_dump_json())
    messages = []
    sink = logs.add(lambda message: messages.append(str(message)), format='{message}',
                    filter='src.trading.backtest.reporting.bundle')
    try:
        ReportingBundle(pm.experiment_dir(experiment_name='empty')).run()
    finally:
        logs.remove(sink)
    text = ''.join(messages)
    assert '本次未提交订单' in text and '成交数量：0 / 0 股（无值）' in text
    assert '夏普 无值' in text and 'insufficient_defined_daily_returns' in text
    persisted = json.loads(pm.experiment_backtest_metrics(experiment_name='empty').read_text())
    assert persisted['execution_eval']['order_completion_rate'] is None
    assert persisted['portfolio']['sharpe'] is None
