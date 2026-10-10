# filepath: tests/trading/backtest/market/test_replay.py
from decimal import Decimal

import pytest

from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput


def market_input() -> ReplayMarketInput:
    return ReplayMarketInput.model_validate({
        "price_basis": "raw", "quantity_unit": "shares",
        "sessions": [{"trade_date": "2026-07-03", "start_ts_us": 0, "close_ts_us": 180_000_000,
                      "windows": [{"trade_date": "2026-07-03", "start_ts_us": 0, "end_ts_us": 180_000_000}]}],
        "marks": [{"ts_us": 1, "trade_date": "2026-07-03", "prices": {"A": "10"}, "kind": "mark"}],
        "observations": [{"observation_id": "bar", "symbol": "A", "price": "11", "volume_qty": 100,
            "time": {"unit": "minute", "size": 1, "trade_date": "2026-07-03", "start_ts_us": 0,
                     "end_ts_us": 60_000_000, "available_ts_us": 60_000_000}}],
    })


def test_starting_after_prior_marks_keeps_the_newest_visible_price() -> None:
    market = ReplayMarket(market_input())
    market.on_time(60_000_000)
    assert market.get_price("A") == Decimal("11")
    assert [o.observation_id for o in market.observations_at(60_000_000)] == ["bar"]


def test_visibility_does_not_depend_on_intermediate_clock_wakeups() -> None:
    one, many = ReplayMarket(market_input()), ReplayMarket(market_input())
    one.on_time(120_000_000)
    for ts in (0, 1, 30_000_000, 60_000_000, 120_000_000):
        many.on_time(ts)
    assert one.prices == many.prices == {"A": Decimal("11")}
    assert one.observations_at(120_000_000) == ()
    with pytest.raises(ValueError, match="regress"):
        many.on_time(1)
