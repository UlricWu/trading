# filepath: tests/trading/backtest/steps/test_prepare_replay_symbols.py
from unittest.mock import Mock, call

from src.access import Access
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.market.replay import ReplaySession
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.prepare_replay_symbols import PrepareReplaySymbolsStep


def test_full_market_keeps_held_and_past_members_without_anticipating_new_listings() -> None:
    access = Mock(spec=Access)
    access.historical_stock_listings.side_effect = [
        {"600000": "1999-11-10", "000001": "2026-09-02", "001246": None},
        {"600000": None, "000001": "2026-09-02", "001246": None},
        {},
    ]
    sessions = tuple(ReplaySession(trade_date=f"2026-09-0{i + 1}", start_ts_us=i * 100,
        close_ts_us=i * 100 + 99, windows=({"trade_date": f"2026-09-0{i + 1}",
        "start_ts_us": i * 100, "end_ts_us": i * 100 + 99},)) for i in range(3))
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
                              sessions=sessions)

    PrepareReplaySymbolsStep(access, symbols=(), held_symbols=("000002",)).run(context)

    assert context.symbols_by_date == {
        "2026-09-01": ("000002", "600000"),
        "2026-09-02": ("000001", "000002", "600000"),
        "2026-09-03": ("000001", "000002", "600000"),
    }
    assert context.unknown_listing_symbols_by_date == {session.trade_date: ("001246",) for session in sessions}
    assert access.historical_stock_listings.call_args_list == [call(trade_date=session.trade_date) for session in sessions]


def test_explicit_selection_does_not_require_historical_lists() -> None:
    access = Mock(spec=Access)
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
                              sessions=(ReplaySession(trade_date="2026-09-01", start_ts_us=0,
        close_ts_us=100, windows=({"trade_date": "2026-09-01", "start_ts_us": 0, "end_ts_us": 100},)),))

    PrepareReplaySymbolsStep(access, symbols=("600000",), held_symbols=()).run(context)

    assert context.symbols_by_date == {"2026-09-01": ("600000",)}
    access.historical_stock_listings.assert_not_called()
    assert context.unknown_listing_symbols_by_date == {}
