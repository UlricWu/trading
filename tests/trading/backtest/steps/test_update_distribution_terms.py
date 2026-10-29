# filepath: tests/trading/backtest/steps/test_update_distribution_terms.py
"""A late reference correction changes valuation without repeating an entitlement."""

from datetime import date, time
from decimal import Decimal

import pytest

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.corporate_actions import CorporateActionSchedule
from src.trading.backtest.market.replay import MarkObservation, ReplayMarket, ReplayMarketInput, ReplaySession
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.update_distribution_terms import UpdateDistributionTermsStep
from src.trading.backtest.steps.apply_distributions import ApplyDistributionsStep
from src.utils.datetime_utils import DateTimeUtils
from src.trading.portfolio.corporate_actions import Distribution, DistributionEntitlement
from src.trading.portfolio.state import PortfolioState


@pytest.mark.parametrize("kind", ["distribution", "compensation"])
@pytest.mark.parametrize("change", ["cancelled", "cash"])
def test_confirmed_account_stages_cannot_be_silently_rewritten(kind: str, change: str) -> None:
    session = ReplaySession(trade_date="2026-07-03", start_ts_us=0, close_ts_us=100,
        windows=({"trade_date": "2026-07-03", "start_ts_us": 0, "end_ts_us": 100},))
    original = Distribution(action_id="event/A", symbol="A", source_ref="fixture://event", kind=kind,
        announced_ts_us=0, record_ts_us=1, ex_ts_us=2, pay_ts_us=2, cash_per_share=1, shares_per_share=0,
        reference_cash_per_share=1, reference_shares_per_share=0)
    revised = original.model_copy(update={"announced_ts_us": 3,
        **({"benefit_state": "cancelled"} if change == "cancelled" else {"cash_per_share": Decimal(2)})})
    entitlement = DistributionEntitlement(distribution=original, eligible_qty=100, recognized=True, cash_paid=True)
    account = PortfolioState(initial_cash=Decimal(0), cash=Decimal(100),
        distribution_entitlements={original.action_id: entitlement})
    state = BacktestState(portfolio_state=account, distribution_terms={original.action_id: original})
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        state=state, tick=TickState(ts_us=3, session=session),
        corporate_actions=CorporateActionSchedule(distributions=(original,), revisions=(revised,)))
    with pytest.raises(ValueError, match="explicit account adjustment"):
        UpdateDistributionTermsStep().run(context)
    assert account.cash == 100 and account.distribution_entitlements[original.action_id] == entitlement
    assert not state.corporate_action_records


@pytest.mark.parametrize("real_mark", [False, True])
@pytest.mark.parametrize("absolute", [False, True])
def test_late_reference_revision_preserves_recognized_cash_shares_and_registration(real_mark: bool, absolute: bool) -> None:
    session = ReplaySession(trade_date="2026-07-03", start_ts_us=0, close_ts_us=100,
        windows=({"trade_date": "2026-07-03", "start_ts_us": 0, "end_ts_us": 100},))
    marks = [MarkObservation(ts_us=1, trade_date=session.trade_date, prices={"A": "10"}, kind="mark")]
    if real_mark:
        marks.append(MarkObservation(ts_us=3, trade_date=session.trade_date, prices={"A": "11"}, kind="mark"))
    market = ReplayMarket(ReplayMarketInput(price_basis="raw", quantity_unit="shares", sessions=(session,),
        marks=tuple(marks), observations=()))
    original = Distribution(action_id="cash/A", symbol="A", source_ref="corporate_actions/v1/day",
        announced_ts_us=0, record_ts_us=1, ex_ts_us=2, pay_ts_us=100, cash_per_share="0.1",
        shares_per_share="0", reference_cash_per_share="0.4", reference_shares_per_share="0")
    revised = original.model_copy(update={"announced_ts_us": 3, "reference_cash_per_share": Decimal("0.04")})
    if absolute:
        revised = Distribution.model_validate({**revised.model_dump(), "reference_cash_per_share": None,
            "reference_shares_per_share": None, "reference_price": "1.80"})
    account = PortfolioState(initial_cash=Decimal(200), cash=Decimal(200), positions={"A": 100},
        avg_cost={"A": Decimal(10)}, distribution_entitlements={original.action_id:
            DistributionEntitlement(distribution=original, eligible_qty=100, recognized=True)})
    state = BacktestState(portfolio_state=account, distribution_terms={original.action_id: original})
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        state=state, market=market, tick=TickState(ts_us=2, session=session),
        corporate_actions=CorporateActionSchedule(distributions=(original,), revisions=(revised,)))
    market.on_time(2)
    market.apply_distribution_reference(action_id=original.action_id, symbol="A", ts_us=2,
        trade_date=session.trade_date, reference_cash_per_share=original.reference_cash_per_share,
        reference_shares_per_share=original.reference_shares_per_share)
    UpdateDistributionTermsStep().run(context)
    assert market.get_price("A") == Decimal("9.6")
    assert not state.corporate_action_records
    market.on_time(3)
    context.tick = TickState(ts_us=3, session=session)
    step = UpdateDistributionTermsStep()
    step.run(context)
    step.run(context)
    assert market.get_price("A") == (Decimal(11) if real_mark else Decimal("1.80" if absolute else "9.96"))
    assert account.cash == 200 and account.dividend_receivable == 10
    assert account.positions == {"A": 100}
    entitlement = account.distribution_entitlements[original.action_id]
    assert entitlement.recognized and entitlement.eligible_qty == 100 and not entitlement.cash_paid
    assert sum(row.event == "terms_updated" for row in state.corporate_action_records) == 1
    assert sum(row.event == "reference_updated" for row in state.corporate_action_records) == int(not real_mark)
    assert all(row.cash_delta == row.receivable_delta == row.shares_delta == 0 for row in state.corporate_action_records)


@pytest.mark.parametrize("real_mark", [False, True])
@pytest.mark.parametrize("absolute", [False, True])
def test_first_late_zero_entitlement_reference_does_not_override_a_post_ex_market_price(
    real_mark: bool, absolute: bool,
) -> None:
    session = ReplaySession(trade_date="2026-07-03", start_ts_us=0, close_ts_us=100,
        windows=({"trade_date": "2026-07-03", "start_ts_us": 0, "end_ts_us": 100},))
    marks = [MarkObservation(ts_us=1, trade_date=session.trade_date, prices={"A": "10"}, kind="mark")]
    if real_mark:
        marks.append(MarkObservation(ts_us=3, trade_date=session.trade_date, prices={"A": "11"}, kind="mark"))
    market = ReplayMarket(ReplayMarketInput(price_basis="raw", quantity_unit="shares", sessions=(session,),
        marks=tuple(marks), observations=()))
    initial = Distribution(action_id="zero/A", symbol="A", source_ref="fixture://zero-rights",
        announced_ts_us=0, record_ts_us=1, ex_ts_us=2, cash_per_share="0", shares_per_share="0",
        reference_cash_per_share=None, reference_shares_per_share=None)
    terms = {"reference_price": "7"} if absolute else {
        "reference_cash_per_share": "0", "reference_shares_per_share": "0"}
    final = Distribution.model_validate({**initial.model_dump(), "announced_ts_us": 4, **terms})
    account = PortfolioState(initial_cash=Decimal(200), cash=Decimal(200), positions={"A": 100},
        avg_cost={"A": Decimal(10)})
    state = BacktestState(portfolio_state=account, distribution_terms={initial.action_id: initial})
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        state=state, market=market, tick=TickState(ts_us=3, session=session),
        corporate_actions=CorporateActionSchedule(distributions=(initial,), revisions=(final,)))
    market.on_time(3)
    step = UpdateDistributionTermsStep()
    step.run(context)
    assert state.distribution_terms[initial.action_id].reference_price is None
    assert market.get_price("A") == Decimal(11 if real_mark else 10)
    context.tick = TickState(ts_us=4, session=session)
    market.on_time(4)
    step.run(context)
    step.run(context)
    mark = market.price_marks["A"]
    assert mark.price == Decimal(11 if real_mark else 7 if absolute else 10)
    assert mark.kind == ("mark" if real_mark else "corporate_action")
    assert mark.ts_us == (3 if real_mark else 4)
    assert account.positions == {"A": 100} and account.cash == 200
    assert account.avg_cost == {"A": Decimal(10)} and not account.distribution_entitlements
    assert sum(row.event == "reference_updated" for row in state.corporate_action_records) == int(not real_mark)


@pytest.fixture
def compensation_context() -> BacktestContext:
    record = DateTimeUtils.local_time_to_utc_epoch_us(time(15), date(2026, 7, 17))
    available = DateTimeUtils.local_time_to_utc_epoch_us(time(10), date(2026, 7, 20))
    session = ReplaySession(trade_date="2026-07-20", start_ts_us=available - 1, close_ts_us=available + 100,
        windows=({"trade_date": "2026-07-20", "start_ts_us": available, "end_ts_us": available + 100},))
    action = Distribution(action_id="compensation/A", symbol="A", source_ref="fixture://compensation",
        kind="compensation", announced_ts_us=available, record_ts_us=record, ex_ts_us=None,
        cash_per_share=1, shares_per_share=0, reference_cash_per_share=None, reference_shares_per_share=None)
    account = PortfolioState(initial_cash=Decimal(0), cash=Decimal(0), positions={"A": 200},
        registration_holdings={"2026-07-17": {"A": 100}})
    return BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        state=BacktestState(portfolio_state=account), tick=TickState(ts_us=available, session=session),
        market=ReplayMarket(ReplayMarketInput(price_basis="raw", quantity_unit="shares", sessions=(session,),
            marks=(), observations=())), corporate_actions=CorporateActionSchedule(distributions=(action,)))


@pytest.mark.parametrize("historical_qty,current_qty", [(100, 0), (0, 100), (60, 160)])
def test_retroactive_eligibility_uses_only_own_historical_holdings(
    compensation_context: BacktestContext, historical_qty: int, current_qty: int,
) -> None:
    context = compensation_context
    account = context.state.portfolio_state
    account.registration_holdings = {"2026-07-17": {"A": historical_qty}}
    account.positions = {"A": current_qty}
    UpdateDistributionTermsStep().run(context)
    UpdateDistributionTermsStep().run(context)
    ApplyDistributionsStep().run(context)
    ApplyDistributionsStep().run(context)
    assert account.dividend_receivable == historical_qty
    assert account.positions == {"A": current_qty} and account.cash == 0
    assert sum(r.event == "registered" for r in context.state.corporate_action_records) == int(historical_qty > 0)
    assert sum(r.event == "recognized" for r in context.state.corporate_action_records) == int(historical_qty > 0)


def test_missing_registration_history_is_not_assumed_to_be_current_or_zero(compensation_context: BacktestContext) -> None:
    context = compensation_context
    context.state.portfolio_state.registration_holdings = {}
    UpdateDistributionTermsStep().run(context)
    ApplyDistributionsStep().run(context)
    assert not context.state.portfolio_state.distribution_entitlements
    assert [i.reason for i in context.state.corporate_action_issues.values()] == ["missing_registration_holdings"]


@pytest.mark.parametrize("cancelled", [False, True])
def test_conditional_and_cancelled_plans_do_not_accrue(compensation_context: BacktestContext, cancelled: bool) -> None:
    context = compensation_context
    original = context.corporate_actions.distributions[0]
    conditional = original.model_copy(update={"benefit_state": "conditional"})
    revisions = (conditional.model_copy(update={"announced_ts_us": original.announced_ts_us + 1,
        "benefit_state": "cancelled"}),) if cancelled else ()
    context.corporate_actions = CorporateActionSchedule(distributions=(conditional,), revisions=revisions)
    UpdateDistributionTermsStep().run(context)
    ApplyDistributionsStep().run(context)
    context.tick.ts_us += 1
    UpdateDistributionTermsStep().run(context)
    ApplyDistributionsStep().run(context)
    assert context.state.portfolio_state.dividend_receivable == 0
    assert not any(r.event == "recognized" for r in context.state.corporate_action_records)


@pytest.mark.parametrize("paid", [False, True])
def test_public_increment_adjusts_only_unsettled_compensation(compensation_context: BacktestContext, paid: bool) -> None:
    context = compensation_context
    original = context.corporate_actions.distributions[0]
    revised = original.model_copy(update={"announced_ts_us": original.announced_ts_us + 1, "cash_per_share": Decimal(2)})
    context.corporate_actions = CorporateActionSchedule(distributions=(original,), revisions=(revised,))
    UpdateDistributionTermsStep().run(context)
    ApplyDistributionsStep().run(context)
    account = context.state.portfolio_state
    assert account.dividend_receivable == 100
    if paid:
        item = account.distribution_entitlements[original.action_id]
        account.distribution_entitlements[original.action_id] = item.model_copy(update={"cash_paid": True})
    context.tick.ts_us += 1
    if paid:
        with pytest.raises(ValueError, match="settled compensation correction"):
            UpdateDistributionTermsStep().run(context)
    else:
        UpdateDistributionTermsStep().run(context)
        UpdateDistributionTermsStep().run(context)
        assert account.dividend_receivable == 200 and account.cash == 0
        adjustment, = [r for r in context.state.corporate_action_records if r.event == "entitlement_revised"]
        assert adjustment.receivable_delta == 100 and adjustment.ts_us == revised.announced_ts_us
        assert sum(r.event == "recognized" for r in context.state.corporate_action_records) == 1


@pytest.mark.parametrize("phase,date_field", [
    ("cash_paid", "pay_ts_us"), ("shares_credited", "shares_credit_ts_us"),
    ("shares_released", "shares_available_ts_us"),
])
def test_settled_compensation_dates_cannot_be_rewritten(
    compensation_context: BacktestContext, phase: str, date_field: str,
) -> None:
    context = compensation_context
    original = context.corporate_actions.distributions[0].model_copy(update={
        "pay_ts_us": context.tick.ts_us, "shares_credit_ts_us": context.tick.ts_us,
        "shares_available_ts_us": context.tick.ts_us})
    revised = original.model_copy(update={"announced_ts_us": original.announced_ts_us + 1,
        date_field: context.tick.ts_us + 1})
    entitlement = DistributionEntitlement(distribution=original, eligible_qty=100, recognized=True,
        **{phase: True, **({"shares_credited": True} if phase == "shares_released" else {})})
    context.state.portfolio_state.distribution_entitlements[original.action_id] = entitlement
    context.state.distribution_terms[original.action_id] = original
    context.corporate_actions = CorporateActionSchedule(distributions=(original,), revisions=(revised,))
    context.tick.ts_us += 1
    with pytest.raises(ValueError, match="settled compensation correction"):
        UpdateDistributionTermsStep().run(context)


@pytest.mark.parametrize("real_mark", [False, True])
@pytest.mark.parametrize("benefit_state", ["conditional", "confirmed", "cancelled"])
def test_first_late_compensation_reference_is_applied_only_at_publication(real_mark: bool, benefit_state: str) -> None:
    session = ReplaySession(trade_date="2026-07-03", start_ts_us=0, close_ts_us=100,
        windows=({"trade_date": "2026-07-03", "start_ts_us": 0, "end_ts_us": 100},))
    marks = [MarkObservation(ts_us=1, trade_date=session.trade_date, prices={"A": "10"}, kind="mark")]
    if real_mark:
        marks.append(MarkObservation(ts_us=4, trade_date=session.trade_date, prices={"A": "11"}, kind="mark"))
    market = ReplayMarket(ReplayMarketInput(price_basis="raw", quantity_unit="shares", sessions=(session,),
        marks=tuple(marks), observations=()))
    action = Distribution(action_id="compensation/A", symbol="A", source_ref="fixture://compensation",
        kind="compensation", benefit_state=benefit_state, announced_ts_us=5, record_ts_us=2, ex_ts_us=3, cash_per_share="0", shares_per_share="0",
        reference_cash_per_share=None, reference_shares_per_share=None, reference_price="7")
    state = BacktestState(PortfolioState(initial_cash=Decimal(0), cash=Decimal(0), positions={"A": 100}))
    context = BacktestContext(metrics=BacktestMetrics(mode="execution_eval", parameters={}, not_run={}),
        state=state, market=market, tick=TickState(ts_us=3, session=session),
        corporate_actions=CorporateActionSchedule(distributions=(action,)))
    market.on_time(3)
    UpdateDistributionTermsStep().run(context)
    assert market.get_price("A") == 10
    context.tick.ts_us = 5
    market.on_time(5)
    UpdateDistributionTermsStep().run(context)
    assert market.get_price("A") == (11 if real_mark else 7 if benefit_state == "confirmed" else 10)
    assert market.price_marks["A"].ts_us == (4 if real_mark else 5 if benefit_state == "confirmed" else 1)
    assert not state.portfolio_state.distribution_entitlements
    assert not state.corporate_action_issues
