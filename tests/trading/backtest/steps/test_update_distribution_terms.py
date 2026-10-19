# filepath: tests/trading/backtest/steps/test_update_distribution_terms.py
"""A late reference correction changes valuation without repeating an entitlement."""

from decimal import Decimal

import pytest

from src.trading.backtest.context import BacktestContext, BacktestState, TickState
from src.trading.backtest.corporate_actions import CorporateActionSchedule
from src.trading.backtest.market.replay import MarkObservation, ReplayMarket, ReplayMarketInput, ReplaySession
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.update_distribution_terms import UpdateDistributionTermsStep
from src.trading.portfolio.corporate_actions import Distribution, DistributionEntitlement
from src.trading.portfolio.state import PortfolioState


@pytest.mark.parametrize("real_mark", [False, True])
def test_late_reference_revision_preserves_recognized_cash_shares_and_registration(real_mark: bool) -> None:
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
    assert market.get_price("A") == (Decimal(11) if real_mark else Decimal("9.96"))
    assert account.cash == 200 and account.dividend_receivable == 10
    assert account.positions == {"A": 100}
    entitlement = account.distribution_entitlements[original.action_id]
    assert entitlement.recognized and entitlement.eligible_qty == 100 and not entitlement.cash_paid
    assert sum(row.event == "terms_updated" for row in state.corporate_action_records) == 1
    assert sum(row.event == "reference_updated" for row in state.corporate_action_records) == int(not real_mark)
    assert all(row.cash_delta == row.receivable_delta == row.shares_delta == 0 for row in state.corporate_action_records)
