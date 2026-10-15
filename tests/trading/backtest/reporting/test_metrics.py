# filepath: tests/trading/backtest/reporting/test_metrics.py
import pytest

from src.trading.backtest.market.replay import MarketDataIssue
from src.trading.backtest.reporting.metrics import BacktestMetrics, MarketDataQuality


def test_diagnostic_status_and_issues_survive_metrics_serialization() -> None:
    issue = MarketDataIssue(ts_us=1, trade_date="2026-09-15", symbol="601238", reason="missing_daily_close")
    metrics = BacktestMetrics(mode="execution_eval", parameters={}, not_run={},
                              data_quality=MarketDataQuality(diagnostic_only=True, issues=(issue,)))
    restored = BacktestMetrics.model_validate_json(metrics.model_dump_json())
    assert restored.data_quality == metrics.data_quality
    assert restored.data_quality.diagnostic_only


@pytest.mark.parametrize("diagnostic_only", [False, True])
def test_market_quality_cannot_hide_an_issue_or_invent_a_diagnostic(diagnostic_only: bool) -> None:
    issue = MarketDataIssue(ts_us=1, trade_date="2026-09-15", symbol="601238", reason="missing_daily_close")
    with pytest.raises(ValueError, match="must reflect recorded"):
        MarketDataQuality(diagnostic_only=diagnostic_only, issues=() if diagnostic_only else (issue,))
