# filepath: tests/trading/backtest/market/test_replay.py
from decimal import Decimal

import pytest

from src.trading.backtest.market.replay import MarketDataIssue, MarkObservation, ReplayMarket, ReplayMarketInput


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


def test_shared_market_marks_cannot_change_between_account_replays() -> None:
    source = market_input()
    baseline, treatment = ReplayMarket(source), ReplayMarket(source)
    baseline.on_time(1)

    with pytest.raises(TypeError):
        source.marks[0].prices["A"] = Decimal("99")

    treatment.on_time(1)
    assert baseline.prices == treatment.prices == {"A": Decimal("10")}
    restored = ReplayMarketInput.model_validate(source.model_dump())
    assert restored == source


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


def test_suspension_is_visible_at_close_without_changing_price_origin() -> None:
    source = market_input().model_copy(update={"observations": (), "marks": (
        MarkObservation(ts_us=180_000_000, trade_date="2026-07-03", prices={}, kind="daily_close",
                        suspended_symbols=frozenset({"A"})),)})
    seed = MarkObservation(ts_us=-1, trade_date="2026-07-02", prices={"A": "9"}, kind="daily_close")
    market = ReplayMarket(source, initial_marks=(seed,))
    assert market.get_price("A") is None
    market.on_time(0)
    assert market.get_price("A") == 9
    assert market.suspended_close_symbols.get(180_000_000) is None
    origin = market.price_marks["A"]
    market.on_time(180_000_000)
    assert market.suspended_close_symbols[180_000_000] == {"A"}
    assert market.price_marks["A"] == origin
    assert origin.ts_us == -1 and origin.trade_date == "2026-07-02"
    assert not market.observations_at(180_000_000)


@pytest.mark.parametrize("prices,kind", [({"A": "10"}, "daily_close"), ({}, "mark")])
def test_suspension_mark_cannot_supply_a_fake_price_or_intraday_status(prices: dict[str, str], kind: str) -> None:
    with pytest.raises(ValueError, match="suspended symbols belong only"):
        MarkObservation(ts_us=1, trade_date="2026-07-03", prices=prices, kind=kind,
                        suspended_symbols=frozenset({"A"}))


def test_data_gap_is_visible_only_at_its_close_and_never_supplies_a_price() -> None:
    issue = MarketDataIssue(ts_us=180_000_000, trade_date="2026-07-03", symbol="A", reason="missing_daily_close")
    mark = MarkObservation(ts_us=issue.ts_us, trade_date=issue.trade_date, prices={},
                           kind="daily_close", data_issues=(issue,))
    source = market_input().model_copy(update={"marks": (mark,), "observations": ()})
    assert ReplayMarketInput.model_validate_json(source.model_dump_json()) == source
    market = ReplayMarket(source)
    market.on_time(179_999_999)
    assert market.daily_close_issues == {}
    market.on_time(180_000_000)
    market.on_time(180_000_000)
    assert market.daily_close_issues == {180_000_000: (issue,)}
    assert market.prices == {}
    assert market.suspended_close_symbols[180_000_000] == frozenset()
    assert market.observations_at(180_000_000) == ()


@pytest.mark.parametrize("change", [{"prices": {"A": "10"}}, {"suspended_symbols": {"A"}},
                                    {"kind": "mark"}, {"ts_us": 179_000_000}])
def test_data_gap_cannot_conflict_with_a_price_suspension_or_visibility(change: dict[str, object]) -> None:
    issue = MarketDataIssue(ts_us=180_000_000, trade_date="2026-07-03", symbol="A", reason="missing_daily_close")
    values = {"ts_us": issue.ts_us, "trade_date": issue.trade_date, "prices": {}, "kind": "daily_close",
              "data_issues": (issue,), **change}
    with pytest.raises(ValueError, match="data issues require"):
        MarkObservation.model_validate(values)


def test_reference_revisions_recompute_from_original_price_without_compounding() -> None:
    market = ReplayMarket(market_input())
    market.on_time(2)
    market.apply_distribution_reference(action_id="cash", symbol="A", ts_us=2, trade_date="2026-07-03",
        reference_cash_per_share=Decimal("0.4"), reference_shares_per_share=Decimal(0))
    assert market.get_price("A") == Decimal("9.6")
    market.on_time(3)
    assert market.revise_distribution_reference(action_id="cash", symbol="A", ts_us=3,
        reference_cash_per_share=Decimal("0.04"), reference_shares_per_share=Decimal(0))
    assert market.get_price("A") == Decimal("9.96")
    assert not market.revise_distribution_reference(action_id="cash", symbol="A", ts_us=3,
        reference_cash_per_share=Decimal("0.04"), reference_shares_per_share=Decimal(0))
    market.on_time(4)
    assert market.revise_distribution_reference(action_id="cash", symbol="A", ts_us=4,
        reference_cash_per_share=Decimal("0.1"), reference_shares_per_share=Decimal("0.1"))
    assert market.get_price("A") == Decimal("9")
    assert market.observations_at(4) == ()


@pytest.mark.parametrize("replacement", ["execution", "other_distribution"])
def test_late_reference_cannot_replace_real_prices_or_another_event(replacement: str) -> None:
    market = ReplayMarket(market_input())
    market.on_time(2)
    market.apply_distribution_reference(action_id="first", symbol="A", ts_us=2, trade_date="2026-07-03",
        reference_cash_per_share=Decimal("0.1"), reference_shares_per_share=Decimal(0))
    if replacement == "execution":
        market.on_time(60_000_000)
    else:
        market.on_time(3)
        market.apply_distribution_reference(action_id="second", symbol="A", ts_us=3, trade_date="2026-07-03",
            reference_cash_per_share=Decimal("0.2"), reference_shares_per_share=Decimal(0))
    prior = market.price_marks["A"]
    assert not market.revise_distribution_reference(action_id="first", symbol="A", ts_us=70_000_000,
        reference_cash_per_share=Decimal("0.04"), reference_shares_per_share=Decimal(0))
    assert market.price_marks["A"] is prior
