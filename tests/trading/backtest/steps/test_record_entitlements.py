# filepath: tests/trading/backtest/steps/test_record_entitlements.py
"""Qualified holdings are fixed at registration, independently of later trades."""
from decimal import Decimal

import pytest

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.corporate_actions import CorporateActionSchedule
from src.trading.backtest.market.replay import ReplaySession
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.record_entitlements import RecordEntitlementsStep
from src.trading.portfolio.corporate_actions import Distribution
from src.trading.portfolio.state import PortfolioState


@pytest.mark.parametrize("ratio", ["0.5", None])
@pytest.mark.parametrize("registered_qty, later_qty", [(0, 100), (100, 0), (60, 160)])
def test_registration_uses_remaining_holdings_and_survives_later_sales(
    registered_qty: int, later_qty: int, ratio: str | None,
) -> None:
    action = Distribution(action_id="ordinary/A", symbol="A", source_ref="fixture://qualified-holdings",
        announced_ts_us=0, record_ts_us=2, ex_ts_us=4, shares_available_ts_us=5,
        cash_per_share="0", shares_per_share=ratio, reference_cash_per_share=None,
        reference_shares_per_share=None, reference_price="1.80")
    session = ReplaySession(trade_date="2026-07-03", start_ts_us=0, close_ts_us=2,
        windows=({"trade_date": "2026-07-03", "start_ts_us": 0, "end_ts_us": 2},))
    account = PortfolioState(initial_cash=Decimal(0), cash=Decimal(0), positions={"A": registered_qty})
    state = BacktestState(portfolio_state=account, distribution_terms={action.action_id: action})
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        state=state, tick=TickState(ts_us=2, session=session), corporate_actions=CorporateActionSchedule(distributions=(action,)))
    step = RecordEntitlementsStep()
    step.run(context)
    step.run(context)
    account.positions["A"] = later_qty
    step.run(context)
    assert account.registration_holdings[session.trade_date] == {"A": registered_qty}
    context.tick = TickState(ts_us=3, session=session)
    step.run(context)
    assert len(state.corporate_action_records) == int(bool(registered_qty))
    if registered_qty:
        entitlement = account.distribution_entitlements[action.action_id]
        assert entitlement.eligible_qty == registered_qty
        if ratio is None:
            assert entitlement.distribution.shares_per_share is None
            with pytest.raises(ValueError, match="not yet known"):
                _ = entitlement.shares_qty
        else:
            assert entitlement.shares_qty == registered_qty // 2
    else:
        assert account.distribution_entitlements == {}


@pytest.mark.parametrize("benefit_state,ratio,recognized", [
    ("confirmed", "0.05", True), ("conditional", "0.05", False), ("confirmed", None, False),
])
def test_known_compensation_is_recognized_after_registration_without_waiting_for_another_tick(
    benefit_state: str, ratio: str | None, recognized: bool,
) -> None:
    action = Distribution(action_id="compensation/A", symbol="A", source_ref="fixture://registration-credit",
        kind="compensation", announced_ts_us=0, record_ts_us=2, ex_ts_us=None,
        shares_credit_ts_us=2, shares_available_ts_us=5, benefit_state=benefit_state,
        cash_per_share="0", shares_per_share=ratio, reference_cash_per_share=None, reference_shares_per_share=None)
    session = ReplaySession(trade_date="2026-07-03", start_ts_us=0, close_ts_us=2,
        windows=({"trade_date": "2026-07-03", "start_ts_us": 0, "end_ts_us": 2},))
    account = PortfolioState(initial_cash=Decimal(0), cash=Decimal(0), positions={"A": 10000})
    state = BacktestState(portfolio_state=account, distribution_terms={action.action_id: action})
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        state=state, tick=TickState(ts_us=2, session=session), corporate_actions=CorporateActionSchedule(distributions=(action,)))
    step = RecordEntitlementsStep()
    step.run(context)
    assert account.positions == {"A": 10000}
    entitlement = account.distribution_entitlements[action.action_id]
    assert entitlement.eligible_qty == 10000 and entitlement.recognized == recognized
    assert not entitlement.shares_credited and not entitlement.shares_released
    if recognized:
        assert entitlement.shares_qty == 500
    account.positions["A"] = 10500
    step.run(context)
    assert account.registration_holdings == {session.trade_date: {"A": 10000}}
    assert account.distribution_entitlements[action.action_id] == entitlement
    assert [row.event for row in state.corporate_action_records] == (
        ["registered", "recognized"] if recognized else ["registered"])
