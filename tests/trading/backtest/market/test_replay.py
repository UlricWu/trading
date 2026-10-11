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


def test_account_day_comes_from_schedule_even_without_bars_or_signals() -> None:
    source = ReplayMarketInput.model_validate({'price_basis': 'raw', 'quantity_unit': 'shares',
        'sessions': [{'trade_date': day, 'start_ts_us': start, 'close_ts_us': start + 10,
                      'windows': [{'trade_date': day, 'start_ts_us': start, 'end_ts_us': start + 10}]}
                     for day, start in (('2026-07-03', 1), ('2026-07-06', 20))],
        'marks': [], 'observations': []})
    assert source.timestamps() == (1, 11, 20, 30)
    assert source.session_at(20).trade_date == '2026-07-06'


@pytest.mark.parametrize(('ts_us', 'trade_date'), [
    (0, '2026-07-03'), (100, '2026-07-03'), (200, '2026-07-06'), (300, '2026-07-06'),
])
def test_input_resolves_session_start_and_close(
    order_market_input: ReplayMarketInput, ts_us: int, trade_date: str,
) -> None:
    assert order_market_input.session_at(ts_us).trade_date == trade_date


@pytest.mark.parametrize('ts_us', [-1, 101, 199, 301])
def test_input_rejects_times_outside_sessions(order_market_input: ReplayMarketInput, ts_us: int) -> None:
    with pytest.raises(ValueError, match='outside the prepared account sessions'):
        order_market_input.session_at(ts_us)


def test_shared_input_preserves_schedule_and_independent_replay_progress() -> None:
    source = market_input()
    original = source.model_dump_json()
    first, second = ReplayMarket(source), ReplayMarket(source)

    first.on_time(60_000_000)
    assert first.get_price('A') == Decimal('11')
    assert second.prices == {}

    second.on_time(1)
    assert second.get_price('A') == Decimal('10')
    assert second.observations_at(1) == ()
    assert [o.observation_id for o in first.observations_at(60_000_000)] == ['bar']

    first.on_time(120_000_000)
    second.on_time(60_000_000)
    assert first.observations_at(120_000_000) == ()
    assert [o.observation_id for o in second.observations_at(60_000_000)] == ['bar']
    assert first.prices == second.prices == {'A': Decimal('11')}
    assert source.timestamps() == (0, 1, 60_000_000, 180_000_000)
    assert source.session_at(0).trade_date == '2026-07-03'
    assert source.model_dump_json() == original
