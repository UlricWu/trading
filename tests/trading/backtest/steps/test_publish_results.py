# filepath: tests/trading/backtest/steps/test_publish_results.py
import json
from pathlib import Path

from src import logs
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.evaluation import evaluate_execution
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.publish_results import PublishResultsStep
from src.trading.core.ledger import ExecutionLedger


def test_publish_omits_unrun_evaluations_but_preserves_undefined_values_and_renders_report(tmp_path: Path) -> None:
    output_dir = tmp_path / 'run'
    metrics = BacktestMetrics(mode='execution_eval', parameters={'mode': 'execution_eval'},
        not_run={'risk': 'no_business_risk', 'signal': 'frozen_commands', 'strategy': 'frozen_commands'},
        execution_eval=evaluate_execution(ExecutionLedger()))
    context = BacktestContext(metrics=metrics)

    messages = []
    sink = logs.add(lambda message: messages.append(str(message)), format='{message}',
                    filter='src.trading.backtest.reporting.bundle')
    try:
        assert PublishResultsStep(output_dir=output_dir).run(context) is context
    finally:
        logs.remove(sink)

    saved = json.loads((output_dir / 'metrics.json').read_text())
    assert saved.keys() == {'mode', 'parameters', 'not_run', 'prediction_refs', 'applied_risk', 'execution_eval'}
    assert saved['applied_risk'] is None
    assert saved['execution_eval']['order_completion_rate'] is None
    assert saved['parameters'] == metrics.parameters and saved['not_run'] == metrics.not_run
    assert saved['prediction_refs'] == []
    html = (output_dir / 'report.html').read_text()
    assert '<h2>成交评价</h2>' in html
    assert 'href="metrics.json"' in html
    assert len(messages) == 1
    assert '✅ 回测报告; experiment=run' in messages[0]
    assert '本次未提交订单' in messages[0]
