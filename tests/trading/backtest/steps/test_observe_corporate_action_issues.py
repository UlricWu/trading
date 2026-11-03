# filepath: tests/trading/backtest/steps/test_observe_corporate_action_issues.py
"""Zero entitlements only cause a price issue while exposed before a usable mark."""
from decimal import Decimal

import pytest

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.market.replay import MarkObservation, ReplayMarket, ReplayMarketInput, ReplaySession
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.observe_corporate_action_issues import ObserveCorporateActionIssuesStep
from src.trading.portfolio.corporate_actions import Distribution, DistributionEntitlement
from src.trading.portfolio.state import PortfolioState


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("mark_ts", [1, 2, 3])
@pytest.mark.parametrize("now", [1, 3])
def test_missing_zero_entitlement_reference_depends_on_exposure_ex_time_and_visible_price(
    held: bool, mark_ts: int, now: int,
) -> None:
    session = ReplaySession(trade_date="2026-07-03", start_ts_us=0, close_ts_us=100,
        windows=({"trade_date": "2026-07-03", "start_ts_us": 0, "end_ts_us": 100},))
    market = ReplayMarket(ReplayMarketInput(price_basis="raw", quantity_unit="shares", sessions=(session,),
        marks=(MarkObservation(ts_us=mark_ts, trade_date=session.trade_date, prices={"A": "10"}, kind="mark"),),
        observations=()))
    action = Distribution(action_id="zero/A", symbol="A", source_ref="fixture://zero-rights",
        announced_ts_us=0, record_ts_us=1, ex_ts_us=2, cash_per_share="0", shares_per_share="0",
        reference_cash_per_share=None, reference_shares_per_share=None)
    account = PortfolioState(initial_cash=Decimal(0), cash=Decimal(0), positions={"A": 100} if held else {})
    state = BacktestState(portfolio_state=account, distribution_terms={action.action_id: action})
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        state=state, market=market, tick=TickState(ts_us=now, session=session))
    market.on_time(now)
    step = ObserveCorporateActionIssuesStep()
    step.run(context)
    step.run(context)
    expected = held and now >= action.ex_ts_us and mark_ts < action.ex_ts_us
    assert len(state.corporate_action_issues) == int(expected)
    if expected:
        issue, = state.corporate_action_issues.values()
        assert issue.reason == "missing_ex_reference"
        assert issue.action_id == action.action_id and issue.ts_us == now
    assert not account.distribution_entitlements


@pytest.mark.parametrize("credit_time", [None, 5])
@pytest.mark.parametrize("recognized", [False, True])
@pytest.mark.parametrize("now", [3, 4])
@pytest.mark.parametrize("shares", ["0", "1"])
def test_unknown_credit_is_reported_for_recognized_stock_after_listing(
    credit_time: int | None, recognized: bool, now: int, shares: str,
) -> None:
    action = Distribution(action_id="compensation/A", symbol="A", source_ref="fixture://pending-credit",
        kind="compensation", announced_ts_us=1, record_ts_us=2, ex_ts_us=None, cash_per_share=0,
        shares_per_share=shares, reference_cash_per_share=None, reference_shares_per_share=None,
        shares_available_ts_us=4, shares_credit_ts_us=credit_time)
    entitlement = DistributionEntitlement(distribution=action, eligible_qty=100, recognized=recognized)
    account = PortfolioState(initial_cash=Decimal(0), cash=Decimal(0),
        distribution_entitlements={action.action_id: entitlement})
    state = BacktestState(portfolio_state=account, distribution_terms={action.action_id: action})
    session = ReplaySession(trade_date="2026-07-03", start_ts_us=0, close_ts_us=100,
        windows=({"trade_date": "2026-07-03", "start_ts_us": 0, "end_ts_us": 100},))
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        state=state, tick=TickState(ts_us=now, session=session))
    step = ObserveCorporateActionIssuesStep()
    step.run(context)
    step.run(context)
    expected = recognized and credit_time is None and now >= 4 and shares != "0"
    assert len(state.corporate_action_issues) == int(expected)
    if expected:
        issue, = state.corporate_action_issues.values()
        assert issue.reason == "pending_compensation_credit"
        assert issue.action_id == action.action_id and issue.ts_us == now
