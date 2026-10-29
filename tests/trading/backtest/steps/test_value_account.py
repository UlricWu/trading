# filepath: tests/trading/backtest/steps/test_value_account.py
from decimal import Decimal

import pytest

from src.trading.backtest.context import BacktestContext, TickState
from src.trading.backtest.market.replay import MarketDataIssue, MarkObservation, ReplayMarket
from src.trading.backtest.steps.value_account import ValueAccountStep
from src.trading.portfolio.corporate_actions import Distribution, DistributionEntitlement


@pytest.mark.parametrize("price_kind", ["daily_close", "mark"])
def test_suspended_close_requires_a_prior_real_close_and_preserves_its_origin(
    tick_context: BacktestContext, price_kind: str,
) -> None:
    context = tick_context
    session = context.inputs.market.sessions[0]
    source = context.inputs.market.model_copy(update={"marks": (
        MarkObservation(ts_us=session.close_ts_us, trade_date=session.trade_date, prices={}, kind="daily_close",
                        suspended_symbols=frozenset({"A"})),)})
    seed = MarkObservation(ts_us=-1, trade_date="2026-07-02", prices={"A": "10"}, kind=price_kind)
    context.market = ReplayMarket(source, initial_marks=(seed,))
    context.market.on_time(session.close_ts_us)
    context.tick = TickState(session.close_ts_us, session)
    context.state.portfolio_state.positions = {"A": 100}
    if price_kind == "mark":
        with pytest.raises(ValueError, match="prior visible raw daily close"):
            ValueAccountStep().run(context)
        return
    ValueAccountStep().run(context)
    mark = context.state.valuation_marks[(session.close_ts_us, "close")][0]
    assert (mark.reason, mark.price, mark.price_ts_us, mark.price_trade_date) == ("suspension", Decimal("10"), -1, "2026-07-02")
    assert context.state.daily_equity[session.trade_date].equity == 21000
    context.state.portfolio_state.positions.clear()
    ValueAccountStep().run(context)
    assert context.state.valuation_marks[(session.close_ts_us, "close")] == ()


def test_zero_holding_does_not_require_a_price(tick_context: BacktestContext) -> None:
    context = tick_context
    context.state.portfolio_state.positions = {"unpriced": 0}
    ValueAccountStep().run(context)
    assert context.state.equity_curve.points[-1].equity == 20000


def test_old_price_does_not_hide_an_unexplained_missing_close(tick_context: BacktestContext) -> None:
    context = tick_context
    session = context.inputs.market.sessions[0]
    source = context.inputs.market.model_copy(update={"marks": context.inputs.market.marks[:1]})
    context.market = ReplayMarket(source)
    context.market.on_time(session.close_ts_us)
    context.tick = TickState(session.close_ts_us, session)
    context.state.portfolio_state.positions = {"A": 100}
    with pytest.raises(ValueError, match="declared raw daily-close marks or confirmed suspension"):
        ValueAccountStep().run(context)


def test_unknown_listing_status_does_not_authorize_held_price_carry(tick_context: BacktestContext) -> None:
    context = tick_context
    session = context.inputs.market.sessions[0]
    issue = MarketDataIssue(ts_us=session.close_ts_us, trade_date=session.trade_date,
                            symbol="A", reason="unknown_listing_status")
    mark = MarkObservation(ts_us=issue.ts_us, trade_date=issue.trade_date, prices={},
                           kind="daily_close", data_issues=(issue,))
    source = context.inputs.market.model_copy(update={"marks": (mark,), "observations": ()})
    seed = MarkObservation(ts_us=-1, trade_date="2026-07-02", prices={"A": "9"}, kind="daily_close")
    context.market = ReplayMarket(source, initial_marks=(seed,))
    context.market.on_time(session.close_ts_us)
    context.tick = TickState(session.close_ts_us, session)
    context.state.portfolio_state.positions = {"A": 100}
    with pytest.raises(ValueError, match="declared raw daily-close marks"):
        ValueAccountStep().run(context)


@pytest.mark.parametrize("has_price", [True, False])
def test_declared_gap_carries_visible_price_but_never_values_an_unpriced_holding(
    tick_context: BacktestContext, has_price: bool,
) -> None:
    context = tick_context
    session = context.inputs.market.sessions[0]
    gap = MarketDataIssue(ts_us=session.close_ts_us, trade_date=session.trade_date,
                          symbol="A", reason="missing_daily_close")
    mark = MarkObservation(ts_us=session.close_ts_us, trade_date=session.trade_date,
                           prices={}, kind="daily_close", data_issues=(gap,))
    source = context.inputs.market.model_copy(update={"marks": (mark,), "observations": ()})
    seed = MarkObservation(ts_us=-1, trade_date="2026-07-02", prices={"A": "9"}, kind="daily_close")
    context.market = ReplayMarket(source, initial_marks=(seed,) if has_price else ())
    context.market.on_time(session.close_ts_us)
    context.tick = TickState(session.close_ts_us, session)
    context.state.portfolio_state.positions = {"A": 100}
    if not has_price:
        with pytest.raises(ValueError, match="holdings require a prior visible raw price.*A"):
            ValueAccountStep().run(context)
        return
    ValueAccountStep().run(context)
    valued = context.state.valuation_marks[(session.close_ts_us, "close")][0]
    assert (valued.price, valued.price_ts_us, valued.reason) == (Decimal("9"), -1, "missing_daily_close")
    assert context.state.daily_equity[session.trade_date].equity == 20900


@pytest.mark.parametrize("price_time", [None, 1, 3])
def test_compensation_share_receivable_keeps_missing_or_stale_marks_visible(
    tick_context: BacktestContext, price_time: int | None,
) -> None:
    context = tick_context
    session = context.tick.session
    marks = () if price_time is None else (MarkObservation(ts_us=price_time,
        trade_date=session.trade_date, prices={"A": "10"}, kind="mark"),)
    context.market = ReplayMarket(context.inputs.market.model_copy(update={"marks": marks}))
    context.market.on_time(context.tick.ts_us)
    action = Distribution(action_id="compensation/A", symbol="A", source_ref="fixture://compensation",
        kind="compensation", announced_ts_us=3, record_ts_us=2, ex_ts_us=None, cash_per_share=0,
        shares_per_share="0.5", reference_cash_per_share=None, reference_shares_per_share=None)
    account = context.state.portfolio_state
    account.distribution_entitlements[action.action_id] = DistributionEntitlement(
        distribution=action, eligible_qty=100, recognized=True)
    ValueAccountStep().run(context)
    result = context.state.equity_curve.points[-1]
    assert result.shares_receivable_value == (0 if price_time is None else 500)
    assert result.equity == (20000 if price_time is None else 20500)
    assert not account.positions
    reasons = {issue.reason for issue in context.state.corporate_action_issues.values()}
    assert reasons == ({"missing_compensation_mark"} if price_time is None or price_time <= 2 else set())
