# filepath: tests/trading/backtest/test_context.py
from src.config.backtest_config import InitialAccount
from src.trading.backtest.components import create_state


def test_runs_own_account_and_collections_without_changing_the_initial_snapshot() -> None:
    initial = InitialAccount(ts_us=1, trade_date='2026-07-03', cash='2000', positions={'A': 100},
                             sellable={'A': 60}, average_cost={'A': '10'}, peak_equity=3000)
    first, second = create_state(initial), create_state(initial)
    first.portfolio_state.positions['A'] = 200
    first.decision_dates[1] = '2026-07-03'
    assert second.portfolio_state.positions == initial.positions == {'A': 100}
    assert second.portfolio_state.t1_sellable('A') == 60
    assert second.decision_dates == {}
    assert second.equity_curve.starting_peak_equity == 3000
