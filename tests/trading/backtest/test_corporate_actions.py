# filepath: tests/trading/backtest/test_corporate_actions.py
"""Reference availability is checked over the complete prepared event timeline."""
import pytest

from src.trading.backtest.corporate_actions import CorporateActionSchedule
from src.trading.portfolio.corporate_actions import Distribution
from src.trading.backtest.market.replay import ReplaySession


def test_zero_entitlements_need_no_registration_or_settlement_when_reference_is_pending() -> None:
    initial = Distribution(action_id="special/A", symbol="A", source_ref="fixture://zero-rights",
        announced_ts_us=0, record_ts_us=2, ex_ts_us=4, cash_per_share="0", shares_per_share="0",
        reference_cash_per_share=None, reference_shares_per_share=None)
    final = initial.model_copy(update={"announced_ts_us": 5, "reference_price": 7})
    schedule = CorporateActionSchedule(distributions=(initial,), revisions=(final,))
    assert schedule.registrations == schedule.payments == schedule.releases == {}
    assert schedule.recognitions == {4: [initial]}
    assert schedule.versions == (initial, final)


@pytest.mark.parametrize("published_ts", [None, 3, 4, 5])
def test_pending_reference_does_not_discard_an_existing_right(published_ts: int | None) -> None:
    initial = Distribution(action_id="special/A", symbol="A", source_ref="fixture://special",
        announced_ts_us=0, record_ts_us=2, ex_ts_us=4, shares_available_ts_us=5,
        cash_per_share="0", shares_per_share="0.5", reference_cash_per_share=None, reference_shares_per_share=None)
    revisions = () if published_ts is None else (Distribution.model_validate({**initial.model_dump(),
        "announced_ts_us": published_ts, "reference_price": "1.80"}),)
    schedule = CorporateActionSchedule(distributions=(initial,), revisions=revisions)
    assert schedule.registrations[2] == [initial]
    assert schedule.versions[0].reference_price is None
    if revisions:
        assert schedule.versions[1].reference_price is not None
    assert all(action.announced_ts_us <= stamp for stamp, actions in schedule.recognitions.items() for action in actions)


@pytest.mark.parametrize("ex_time", [None, 4])
def test_late_compensation_dates_do_not_schedule_ticks_before_publication(ex_time: int | None) -> None:
    session = ReplaySession(trade_date="2026-07-17", start_ts_us=0, close_ts_us=100,
        windows=({"trade_date": "2026-07-17", "start_ts_us": 1, "end_ts_us": 100},))
    action = Distribution(action_id="compensation/A", symbol="A", source_ref="fixture://compensation",
        announced_ts_us=50, record_ts_us=2, ex_ts_us=ex_time, kind="compensation",
        cash_per_share=1, shares_per_share=1, pay_ts_us=10, shares_credit_ts_us=20, shares_available_ts_us=30,
        reference_cash_per_share=None, reference_shares_per_share=None)
    schedule = CorporateActionSchedule(distributions=(action,))
    assert set(schedule.timestamps((session,))) == {50}
