# filepath: tests/trading/backtest/test_evaluation.py
import json
from pathlib import Path

import numpy as np
import pytest

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
    for ts, value in ((1, 100), (3, 110), (5, 104.5)):
        sparse.append(ts_us=ts, cash=value, market_value=0)
    for ts, value in ((1, 100), (2, 100), (3, 110), (4, 110), (5, 104.5)):
        dense.append(ts_us=ts, cash=value, market_value=0)
    daily = {'2026-07-02': sparse.points[1], '2026-07-03': sparse.points[2]}
    sparse_result = evaluate_portfolio(
        sparse, initial_equity=100, daily_equity=daily, ledger=ExecutionLedger(), annual_factor=252,
    )
    dense_result = evaluate_portfolio(
        dense, initial_equity=100, daily_equity=daily, ledger=ExecutionLedger(), annual_factor=252,
    )

    assert sparse_result == dense_result
    assert sparse_result.daily_returns == pytest.approx({'2026-07-02': 0.1, '2026-07-03': -0.05})
    assert sparse_result.total_return == pytest.approx(0.045)
    assert sparse_result.sharpe == pytest.approx(3.7416573867739413)


@pytest.mark.parametrize(
    ('day_count', 'sharpe_reason'),
    [(0, 'insufficient_defined_daily_returns'),
     (1, 'insufficient_defined_daily_returns'),
     (2, 'zero_daily_volatility')],
)
def test_flat_account_preserves_zero_return_and_undefined_sharpe(
    day_count: int, sharpe_reason: str,
) -> None:
    curve = EquityCurve()
    curve.append(ts_us=0, cash=100, market_value=0)
    daily = {}
    for index, day in enumerate(('2026-07-02', '2026-07-03')[:day_count], start=1):
        curve.append(ts_us=index, cash=100, market_value=0)
        daily[day] = curve.points[-1]

    result = evaluate_portfolio(
        curve, initial_equity=100, daily_equity=daily, ledger=ExecutionLedger(), annual_factor=252,
    )
    payload = json.loads(result.model_dump_json())

    assert payload['net_profit'] == payload['total_return'] == payload['gross_return'] == 0
    assert payload['daily_returns'] == dict.fromkeys(daily, 0)
    assert payload['turnover'] == 0
    assert payload['sharpe'] is None
    assert payload['undefined_reasons'] == {'sharpe': sharpe_reason}


@pytest.mark.parametrize('initial_equity', [0.0, -100.0])
def test_nonpositive_initial_equity_preserves_undefined_return_reasons(initial_equity: float) -> None:
    curve = EquityCurve()
    curve.append(ts_us=0, cash=initial_equity, market_value=0)
    curve.append(ts_us=1, cash=initial_equity, market_value=0)

    result = evaluate_portfolio(
        curve, initial_equity=initial_equity, daily_equity={'2026-07-03': curve.points[-1]},
        ledger=ExecutionLedger(), annual_factor=252,
    )
    payload = json.loads(result.model_dump_json())

    assert payload['net_profit'] == 0
    assert payload['total_return'] is None and payload['gross_return'] is None
    assert payload['daily_returns'] == {'2026-07-03': None}
    assert payload['turnover'] is None and payload['sharpe'] is None
    assert payload['undefined_reasons'] == {
        'returns': 'nonpositive_initial_equity',
        'turnover': 'nonpositive_average_equity',
        'sharpe': 'insufficient_defined_daily_returns',
    }
