# filepath: tests/trading/backtest/conftest.py
import pytest

from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput


@pytest.fixture
def order_market_input() -> ReplayMarketInput:
    return ReplayMarketInput.model_validate({
        'price_basis': 'raw', 'quantity_unit': 'shares',
        'sessions': [
            {'trade_date': day, 'start_ts_us': start, 'close_ts_us': start + 100,
             'windows': [{'trade_date': day, 'start_ts_us': start, 'end_ts_us': start + 100}]}
            for day, start in (('2026-07-03', 0), ('2026-07-06', 200))
        ],
        'marks': [], 'observations': [],
    })


@pytest.fixture
def order_market(order_market_input: ReplayMarketInput) -> ReplayMarket:
    return ReplayMarket(order_market_input)
