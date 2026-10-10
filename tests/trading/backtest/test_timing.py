# filepath: tests/trading/backtest/test_timing.py
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput


def test_account_day_comes_from_schedule_even_without_bars_or_signals() -> None:
    market = ReplayMarket(ReplayMarketInput.model_validate({'price_basis': 'raw', 'quantity_unit': 'shares',
        'sessions': [{'trade_date': day, 'start_ts_us': start, 'close_ts_us': start + 10,
                      'windows': [{'trade_date': day, 'start_ts_us': start, 'end_ts_us': start + 10}]}
                     for day, start in (('2026-07-03', 1), ('2026-07-06', 20))],
        'marks': [], 'observations': []}))
    assert market.timestamps() == (1, 11, 20, 30)
    assert market.session_at(20).trade_date == '2026-07-06'
