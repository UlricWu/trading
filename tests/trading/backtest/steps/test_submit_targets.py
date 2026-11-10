# filepath: tests/trading/backtest/steps/test_submit_targets.py
from decimal import Decimal
from unittest.mock import Mock

import pytest

from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput
from src.trading.backtest.steps.submit_targets import SubmitTargetsStep, build_new_order_terms
from src.trading.core.events import CancelOrders, OrderIntent, OrderTerms, Side, TargetEvent
from src.trading.core.time import US_PER_MINUTE

M = US_PER_MINUTE


@pytest.mark.parametrize('target_quantity', [0, 60, 100])
def test_reducing_or_retaining_pending_orders_needs_no_new_calendar_coverage(
    state: BacktestState, market_input: ReplayMarketInput, executor: AccountExecutionSimulator,
    target_quantity: int,
) -> None:
    original = OrderIntent(0, 'A', Side.BUY, 100, 1, 0, 3 * M, Decimal('10'))
    executor.submit_orders(orders=(original,))
    target = TargetEvent(M, {'A': target_quantity})
    terms = build_new_order_terms(target=target, state=state, order_window_minutes=2, windows=())
    assert terms == {}

    market = ReplayMarket(market_input)
    market.on_time(M)
    executor.submit_targets(target=target, cancel_order_ids=(), terms=terms, market=market)
    if target_quantity:
        pending, = state.pending_orders.values()
        assert pending.remaining_qty == target_quantity
        assert pending.intent.active_ts_us == (M if target_quantity < original.qty else original.active_ts_us)
        assert pending.intent.expires_ts_us == original.expires_ts_us
        assert pending.intent.limit_price == original.limit_price
    else:
        assert state.pending_orders == {}


def test_new_terms_cover_additions_and_ignore_opposite_side_orders(
    state: BacktestState, market_input: ReplayMarketInput, executor: AccountExecutionSimulator,
) -> None:
    state.portfolio_state.positions['B'] = 100
    state.portfolio_state.avg_cost['B'] = Decimal('10')
    orders = (OrderIntent(0, 'A', Side.BUY, 100, 1, 0, 2 * M, Decimal('10')),
              OrderIntent(0, 'B', Side.SELL, 50, 2, 0, 2 * M, Decimal('10')))
    executor.submit_orders(orders=orders)
    terms = build_new_order_terms(target=TargetEvent(M, {'A': 160, 'B': 150}), state=state,
        order_window_minutes=2, windows=market_input.sessions[0].windows)

    assert terms == {'A': OrderTerms(M, 3 * M), 'B': OrderTerms(M, 3 * M)}
    assert tuple(order.intent for order in state.pending_orders.values()) == orders


def test_submission_failure_preserves_original_error_and_does_not_publish_observations(
    tick_context: BacktestContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError('submission unavailable')
    tick_context.tick.execution_target = TargetEvent(M, {'A': 100})
    monkeypatch.setattr(AccountExecutionSimulator, 'submit_targets', Mock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        SubmitTargetsStep(order_window_minutes=2).run(tick_context)
    assert caught.value is error
    assert tick_context.state.risk_observations == []
    assert tick_context.state.execution_ledger.commands == []


def test_cancellation_without_target_does_not_create_a_new_order(tick_context: BacktestContext) -> None:
    order = OrderIntent(0, 'A', Side.BUY, 100, 1, 0, 3 * M, Decimal('10'))
    tick_context.executor.submit_orders(orders=(order,))
    tick_context.tick.cancel_order_ids = (1,)
    assert SubmitTargetsStep(order_window_minutes=2).run(tick_context) is tick_context
    assert tick_context.state.pending_orders == {}
    assert tick_context.state.execution_ledger.commands == [order, CancelOrders(M, (1,))]
