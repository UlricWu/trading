# filepath: tests/trading/backtest/test_evaluation.py
from pathlib import Path

import numpy as np

from src.trading.backtest.evaluation import evaluate_portfolio, evaluate_signals
from src.trading.core.equity import EquityCurve
from src.trading.core.ledger import ExecutionLedger
from src.training.evaluation import evaluate_predictions


def test_signal_evaluation_retains_undefined_reasons_and_weights_days_equally() -> None:
    observations = tuple(evaluate_predictions(model_ref=Path('/model'), decision_ts_us=ts,
        predictions=np.array(x, dtype=float), labels=np.array(y, dtype=float))
        for ts, x, y in ((1, [], []), (2, [0], [1]), (3, [0, 1], [0, 1]),
                         (4, [0, 1], [1, 0]), (5, [0, 1], [0, 1])))
    summary = evaluate_signals(observations, decision_dates={1: '2026-07-01', 2: '2026-07-01',
        3: '2026-07-02', 4: '2026-07-02', 5: '2026-07-03'})
    assert summary.decision_count == 5 and summary.trade_day_count == 3
    assert summary.defined_day_count == 2 and summary.mean_ic == .5
    assert summary.daily_ic == {'2026-07-01': None, '2026-07-02': 0, '2026-07-03': 1}
    assert summary.undefined_reason_counts == {'insufficient_pairs': 2, 'constant_prediction': 1, 'constant_label': 1}


def test_unchanged_event_wakeups_cannot_change_day_sampled_returns_or_sharpe() -> None:
    sparse, dense = EquityCurve(), EquityCurve()
    for ts, value in ((1, 100), (3, 110), (5, 99)):
        sparse.append(ts_us=ts, cash=value, market_value=0)
    for ts, value in ((1, 100), (2, 100), (3, 110), (4, 110), (5, 99)):
        dense.append(ts_us=ts, cash=value, market_value=0)
    daily = {'2026-07-02': sparse.points[1], '2026-07-03': sparse.points[2]}
    assert evaluate_portfolio(sparse, initial_equity=100, daily_equity=daily, ledger=ExecutionLedger(), annual_factor=252) == (
        evaluate_portfolio(dense, initial_equity=100, daily_equity=daily, ledger=ExecutionLedger(), annual_factor=252))
