# filepath: tests/trading/backtest/execution/test_fake.py
from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal
from itertools import chain, groupby

import pytest

from src.trading.core.events import CancelOrders, OrderIntent, OrderStatus, OrderTerms, Side, TargetEvent
from src.trading.core.ledger import ExecutionLedger
from src.trading.backtest.clock import ReplayClock
from src.trading.core.time import BarTime, US_PER_MINUTE
from src.trading.backtest.execution.fake import FakeExecution
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.execution.policies.constraints import QuantityRules, QuoteRule
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.venue.fake import LimitPriceVenue, QuotedPriceVenue, ReferenceVenue
from src.trading.backtest.market.data_view import ExecutionObservation
from src.trading.portfolio.state import PortfolioState
from src.utils.datetime_utils import DateTimeUtils

M = US_PER_MINUTE
T = DateTimeUtils.local_time_to_utc_epoch_us(time(14, 30), date(2026, 7, 3))


@dataclass
class Market:
    prices: dict[str, float]

    def get_price(self, symbol: str) -> float | None:
        return self.prices.get(symbol)


def _execution(*, cash: int = 20_000, reference: bool = False,
               slippage: int = 0, commission: float = 0, minimum: int = 0) -> FakeExecution:
    venue = ReferenceVenue() if reference else LimitPriceVenue(FixedBPSlippageModel(slippage))
    return FakeExecution(
        portfolio=PortfolioState(cash, cash, current_day="2026-07-03"),
        ledger=ExecutionLedger(), pending_orders={},
        settlement=SettlementEngine(AShareCostModel(commission, 0, 0, minimum)),
        venue=venue,
        rules={symbol: QuantityRules(1, 1, 1, 1, 1, Decimal("0.01"), True)
               for symbol in ("A", "B")},
        quote_rule=None if reference else QuoteRule(Decimal("0"), Decimal("0")),
    )


def _order(order_id: int = 1, *, qty: int = 1000, symbol: str = "A",
           side: Side = Side.BUY, limit: str | None = "10",
           created: int = T, active: int = T, expires: int = T + 2 * M) -> OrderIntent:
    return OrderIntent(created, symbol, side, qty, order_id, active, expires,
                       None if limit is None else Decimal(limit))


def _observation(end: int, *, price: str = "10", volume: int | None = 300,
                 symbol: str = "A") -> ExecutionObservation:
    return ExecutionObservation(f"{symbol}:{end}", symbol,
        BarTime("minute", 1, "2026-07-03", T + (end - 1) * M, T + end * M, T + end * M),
        Decimal(price), volume)


def test_quoted_fills_settle_at_activation_without_market_observations() -> None:
    execution = _execution(cash=1005, minimum=5)
    execution.venue = QuotedPriceVenue()
    execution.submit_orders(orders=(_order(qty=100, active=T + M),), market=Market({"A": 99}))
    assert execution.portfolio.cash == 1005
    assert not execution.advance(ts_us=T, observations=())
    fills = execution.advance(ts_us=T + M, observations=())
    assert [(fill.ts_us, fill.qty, fill.price, fill.observation_id) for fill in fills] == [
        (T + M, 100, Decimal("10"), None),
    ]
    assert execution.portfolio.cash == 0
    assert execution.portfolio.positions == {"A": 100}
    assert not execution.pending_orders
    assert not execution.advance(ts_us=T + M, observations=())
    assert execution.ledger.order_costs[1].total == 5


def test_quoted_fills_keep_cash_t1_and_quantity_constraints() -> None:
    execution = _execution(cash=1005, minimum=5)
    execution.venue = QuotedPriceVenue()
    market = Market({"A": 10})
    execution.submit_targets(target=TargetEvent(T, {"A": 1000}), cancel_order_ids=(),
        terms={"A": OrderTerms(T, T + M)}, market=market)
    assert execution.portfolio.cash == 0
    assert execution.portfolio.positions == {"A": 100}
    assert execution.ledger.orders_by_id[1].qty == 100
    rejected = execution.submit_orders(orders=(
        _order(2, qty=1), _order(3, qty=100, side=Side.SELL),
    ), market=market)
    assert [event.reason for event in rejected] == ["insufficient_cash", "insufficient_sellable"]
    execution.portfolio.on_trading_day_start("2026-07-06")
    execution.submit_orders(orders=(_order(4, qty=100, side=Side.SELL),), market=market)
    assert execution.portfolio.cash == 995
    assert not execution.portfolio.positions


def test_quoted_sale_rejects_unfunded_fees() -> None:
    execution = _execution(cash=0, minimum=15)
    execution.venue = QuotedPriceVenue()
    execution.portfolio.positions["A"] = 1
    execution.portfolio.avg_cost["A"] = Decimal("10")
    events = execution.submit_orders(orders=(_order(qty=1, side=Side.SELL),), market=Market({"A": 10}))
    assert events[0].reason == "insufficient_cash_for_fees"
    assert not execution.ledger.fills_by_id
    assert execution.portfolio.positions == {"A": 1}


def test_quoted_targets_satisfy_both_order_and_fill_steps() -> None:
    execution = _execution()
    execution.venue = QuotedPriceVenue()
    execution.rules = {"A": QuantityRules(100, 100, 1, 1, 60, Decimal("0.01"), True)}
    market = Market({"A": 10})
    execution.submit_targets(target=TargetEvent(T, {"A": 400}), cancel_order_ids=(),
        terms={"A": OrderTerms(T, T + M)}, market=market)
    assert execution.portfolio.positions == {"A": 300}
    assert next(iter(execution.ledger.fills_by_id.values())).qty == 300
    rejected = execution.submit_orders(orders=(_order(2, qty=100),), market=market)
    assert rejected[0].reason == "quantity_rule"


def test_quoted_pending_sale_reserves_fee_shortfall_until_cancelled() -> None:
    execution = _execution(cash=16, minimum=15)
    execution.venue = QuotedPriceVenue()
    execution.portfolio.positions["A"] = 1
    execution.portfolio.avg_cost["A"] = Decimal("10")
    market = Market({"A": 10, "B": 1})
    execution.submit_orders(orders=(_order(qty=1, side=Side.SELL, active=T + M),), market=market)
    assert execution.pending_orders[1].reserved_cash == 5
    events = execution.submit_orders(orders=(_order(2, qty=1, symbol="B", limit="0.01"),), market=market)
    assert events[0].reason == "insufficient_cash"
    execution.cancel_orders(ts_us=T, order_ids=(1,))
    assert not execution.advance(ts_us=T + M, observations=())
    assert execution.portfolio.cash == 16
    assert execution.portfolio.positions == {"A": 1}


def test_quoted_execution_cannot_backdate_a_missed_activation() -> None:
    execution = _execution()
    execution.venue = QuotedPriceVenue()
    execution.submit_orders(orders=(_order(active=T + M),), market=Market({"A": 10}))
    with pytest.raises(ValueError, match="clock tick at every order activation"):
        execution.advance(ts_us=T + 2 * M, observations=())
    assert not execution.ledger.fills_by_id
    assert execution.portfolio.cash == 20000


def test_partial_fills_last_minute_then_expiry_and_no_automatic_reentry() -> None:
    execution = _execution()
    market = Market({"A": 10})
    execution.submit_targets(target=TargetEvent(T, {"A": 400}), cancel_order_ids=(),
        terms={"A": OrderTerms(T, T + 2 * M)}, market=market)
    assert not execution.ledger.fills_by_id
    assert execution.pending_orders[1].reserved_cash == 4000
    first = execution.advance(ts_us=T + M, observations=(_observation(1),))
    last = execution.advance(ts_us=T + 2 * M, observations=(_observation(2, volume=60),))
    execution.advance(ts_us=T + 3 * M, observations=())
    assert [fill.qty for fill in first + last] == [300, 60]
    assert execution.portfolio.cash == 16_400
    assert execution.portfolio.positions == {"A": 360}
    assert not execution.pending_orders
    assert execution.ledger.order_events[-1].status == OrderStatus.EXPIRED
    assert execution.ledger.order_events[-1].remaining_qty == 40
    assert len(execution.ledger.orders_by_id) == 1


@pytest.mark.parametrize("side,price,slippage,matches", [
    (Side.BUY, "10.05", 0, False), (Side.SELL, "9.98", 0, False),
    (Side.BUY, "10", 0, True), (Side.SELL, "10", 0, True),
    (Side.BUY, "10", 10, False), (Side.SELL, "10", 10, False),
    (Side.BUY, "9.98", 0, True), (Side.SELL, "10.02", 0, True),
])
def test_price_model_checks_slippage_before_limit(side: Side, price: str,
                                               slippage: int, matches: bool) -> None:
    execution = _execution(slippage=slippage)
    if side == Side.SELL:
        execution.portfolio.positions["A"] = 1000
        execution.portfolio.avg_cost["A"] = Decimal("10")
    execution.submit_orders(orders=(_order(side=side),), market=Market({"A": 10}))
    fills = execution.advance(ts_us=T + M, observations=(_observation(1, price=price),))
    assert bool(fills) == matches
    if not matches:
        event = execution.ledger.order_events[-1]
        assert event.reason == "price_not_matched"
        assert event.status == OrderStatus.PENDING
        assert event.candidate_price != execution.ledger.orders_by_id[1].limit_price or slippage == 0


def test_reference_execution_ignores_quote_and_volume_but_not_cash() -> None:
    execution = _execution(cash=1000, reference=True)
    execution.submit_orders(orders=(_order(qty=100, limit=None),), market=Market({"A": 10}))
    fills = execution.advance(ts_us=T + M, observations=(_observation(1, price="10.05", volume=0),))
    assert fills[0].qty == 99
    assert fills[0].price == Decimal("10.05")
    assert execution.portfolio.cash == Decimal("5.05")
    assert execution.pending_orders[1].reserved_cash == Decimal("5.05")


def test_shared_volume_is_allocated_by_active_time_then_submission_order() -> None:
    execution = _execution()
    first = _order(9, qty=200, active=T + M)
    second = _order(2, qty=200)
    third = _order(1, qty=200)
    execution.submit_orders(orders=(first, second, third), market=Market({"A": 10}))
    fills = execution.advance(ts_us=T + 2 * M, observations=(_observation(2, volume=350),))
    assert [(fill.order_id, fill.qty) for fill in fills] == [(2, 200), (1, 150)]
    assert sum(fill.qty for fill in fills) == 350


def test_same_target_is_covered_by_existing_holdings_and_pending() -> None:
    execution = _execution()
    execution.portfolio.positions["A"] = 600
    execution.portfolio.avg_cost["A"] = Decimal("10")
    market = Market({"A": 10})
    terms = {"A": OrderTerms(T, T + 2 * M)}
    execution.submit_targets(target=TargetEvent(T, {"A": 1000}), cancel_order_ids=(), terms=terms, market=market)
    execution.submit_targets(target=TargetEvent(T + M, {"A": 1000}), cancel_order_ids=(), terms={}, market=market)
    assert len(execution.ledger.orders_by_id) == 1
    assert execution.pending_orders[1].remaining_qty == 400


def test_risk_reduction_preserves_quote_and_expiry_before_cancelling() -> None:
    execution = _execution()
    execution.portfolio.positions["A"] = 600
    execution.portfolio.avg_cost["A"] = Decimal("10")
    execution.submit_orders(orders=(_order(qty=400),), market=Market({"A": 10}))
    execution.submit_targets(target=TargetEvent(T + M, {"A": 800}), cancel_order_ids=(1,),
        terms={}, market=Market({"A": 10.20}))
    replacement = execution.pending_orders[2].intent
    assert (replacement.qty, replacement.limit_price) == (200, Decimal("10"))
    assert (replacement.active_ts_us, replacement.expires_ts_us) == (T + M, T + 2 * M)
    assert isinstance(execution.ledger.commands[1], CancelOrders)
    assert execution.ledger.commands[2] == replacement


def test_reverse_target_uses_new_sell_quote_and_keeps_old_position_until_fill() -> None:
    execution = _execution()
    execution.portfolio.positions["A"] = 600
    execution.portfolio.avg_cost["A"] = Decimal("10")
    execution.submit_orders(orders=(_order(qty=400),), market=Market({"A": 10}))
    execution.submit_targets(target=TargetEvent(T + M, {"A": 500}), cancel_order_ids=(1,),
        terms={"A": OrderTerms(T + M, T + 3 * M)}, market=Market({"A": 10.20}))
    order = execution.pending_orders[2].intent
    assert (order.side, order.qty, order.limit_price) == (Side.SELL, 100, Decimal("10.20"))
    assert execution.portfolio.positions == {"A": 600}
    assert execution.portfolio.cash == 20_000


def test_multiple_reduced_orders_retain_individual_windows_and_quotes() -> None:
    execution = _execution()
    execution.submit_orders(orders=(_order(1, qty=100, limit="9"),
        _order(2, qty=100, limit="10", expires=T + 3 * M)), market=Market({"A": 10}))
    execution.submit_targets(target=TargetEvent(T + M, {"A": 150}), cancel_order_ids=(),
        terms={}, market=Market({"A": 11}))
    assert execution.pending_orders[1].intent.limit_price == Decimal("9")
    assert execution.pending_orders[3].intent.limit_price == Decimal("10")
    assert execution.pending_orders[3].intent.expires_ts_us == T + 3 * M
    assert execution.pending_orders[3].remaining_qty == 50


def test_frozen_orders_reject_full_quantity_instead_of_clipping() -> None:
    execution = _execution(cash=1000)
    events = execution.submit_orders(orders=(_order(qty=101),), market=Market({"A": 9}))
    assert events[0].status == OrderStatus.REJECTED
    assert events[0].remaining_qty == 101
    assert not execution.pending_orders


def test_funding_uses_buy_quote_plus_fee_and_releases_on_cancel() -> None:
    execution = _execution(cash=2005, minimum=5)
    events = execution.submit_orders(orders=(_order(1, qty=100), _order(2, qty=100)), market=Market({"A": 9}))
    assert [event.status for event in events] == [OrderStatus.PENDING, OrderStatus.REJECTED]
    assert execution.pending_orders[1].reserved_cash == 1005
    execution.cancel_orders(ts_us=T, order_ids=(1,))
    accepted = execution.submit_orders(orders=(_order(3, qty=200),), market=Market({"A": 9}))
    assert accepted[0].status == OrderStatus.PENDING


def test_t1_and_reserved_sell_quantity_cannot_be_reused() -> None:
    execution = _execution()
    execution.portfolio.positions["A"] = 300
    execution.portfolio.avg_cost["A"] = Decimal("10")
    execution.portfolio.buy_qty_today["A"] = 100
    events = execution.submit_orders(orders=(
        _order(1, qty=200, side=Side.SELL), _order(2, qty=1, side=Side.SELL)), market=Market({"A": 10}))
    assert [event.status for event in events] == [OrderStatus.PENDING, OrderStatus.REJECTED]
    assert execution.pending_orders[1].reserved_sell_qty == 200


def test_minimum_commission_is_charged_once_per_order() -> None:
    execution = _execution(commission=0.0003, minimum=5)
    execution.submit_orders(orders=(_order(qty=200),), market=Market({"A": 10}))
    execution.advance(ts_us=T + M, observations=(_observation(1, volume=100),))
    execution.advance(ts_us=T + 2 * M, observations=(_observation(2, volume=100),))
    costs = [row["commission"] for row in execution.ledger.records if row["event"] == "FILL"]
    assert costs == ["5", "0"]
    assert execution.portfolio.cash == 17_995
    assert execution.ledger.order_costs[1].total == 5


def test_same_observation_replay_cannot_consume_quantity_or_cash_twice() -> None:
    execution = _execution()
    execution.submit_orders(orders=(_order(),), market=Market({"A": 10}))
    observation = _observation(1)
    execution.advance(ts_us=T + M, observations=(observation, observation))
    assert execution.advance(ts_us=T + M, observations=(observation,)) == ()
    assert execution.pending_orders[1].remaining_qty == 700
    assert execution.portfolio.cash == 17_000
    with pytest.raises(ValueError, match="conflicting observation"):
        execution.advance(ts_us=T + M, observations=(replace(observation, price=Decimal("9")),))
    assert len(execution.ledger.fills_by_id) == 1


def test_incomplete_interval_cannot_fill_new_order_and_no_observation_still_expires() -> None:
    execution = _execution()
    execution.submit_orders(orders=(_order(active=T + M // 2),), market=Market({"A": 10}))
    assert execution.advance(ts_us=T + M, observations=(_observation(1),)) == ()
    execution.advance(ts_us=T + 2 * M, observations=())
    assert execution.portfolio.positions == {}
    assert execution.ledger.order_events[-1].reason == "expired:no_observation"


def test_delayed_or_overlapping_liquidity_fails_before_settlement() -> None:
    execution = _execution()
    execution.submit_orders(orders=(_order(),), market=Market({"A": 10}))
    observation = _observation(1)
    late = replace(observation, time=replace(observation.time, available_ts_us=T + M + 2))
    with pytest.raises(ValueError, match="complete observations"):
        execution.advance(ts_us=T + M, observations=(late,))
    duplicate_stream = replace(observation, observation_id="another-source")
    with pytest.raises(ValueError, match="overlapping"):
        execution.advance(ts_us=T + M, observations=(observation, duplicate_stream))
    assert execution.portfolio.cash == 20_000
    assert not execution.ledger.fills_by_id


def test_no_future_sale_proceeds_can_fund_submitted_buy() -> None:
    execution = _execution(cash=0)
    execution.portfolio.positions["A"] = 100
    execution.portfolio.avg_cost["A"] = Decimal("10")
    events = execution.submit_orders(orders=(
        _order(1, qty=100, side=Side.SELL), _order(2, qty=100, symbol="B")),
        market=Market({"A": 10, "B": 10}))
    assert events[1].reason == "insufficient_cash"
    execution.advance(ts_us=T + M, observations=(_observation(1, volume=100),))
    assert execution.portfolio.cash == 1000
    events = execution.submit_orders(orders=(_order(3, qty=100, symbol="B", created=T + M,
        active=T + M),), market=Market({"B": 10}))
    assert events[0].status == OrderStatus.PENDING


def test_dynamic_clock_expires_without_bars_and_stops_at_explicit_endpoint() -> None:
    execution = _execution()
    clock = ReplayClock.from_iterable((T, T + 3 * M))
    visited: list[int] = []
    for ts_us in clock:
        visited.append(ts_us)
        execution.advance(ts_us=ts_us, observations=())
        if ts_us == T:
            execution.submit_orders(orders=(_order(),), market=Market({"A": 10}))
            clock.schedule(ts_us=execution.pending_orders[1].intent.expires_ts_us)
    assert visited == [T, T + 2 * M, T + 3 * M]
    assert execution.ledger.order_events[-1].status == OrderStatus.EXPIRED
    assert execution.portfolio.cash == 20_000


def test_frozen_command_replay_matches_target_submission() -> None:
    original = _execution()
    market = Market({"A": 10})
    original.submit_targets(target=TargetEvent(T, {"A": 1000}), cancel_order_ids=(),
        terms={"A": OrderTerms(T, T + 2 * M)}, market=market)
    original.advance(ts_us=T + M, observations=(_observation(1),))
    original.submit_targets(target=TargetEvent(T + M, {"A": 500}), cancel_order_ids=(1,),
        terms={}, market=Market({"A": 11}))
    original.advance(ts_us=T + 2 * M, observations=(_observation(2, volume=100),))
    replay = _execution()
    for ts_us in (T, T + M, T + 2 * M):
        observations = () if ts_us == T else (_observation((ts_us - T) // M,
            volume=300 if ts_us == T + M else 100),)
        replay.advance(ts_us=ts_us, observations=observations)
        for command in original.ledger.commands:
            if command.ts_us != ts_us:
                continue
            if isinstance(command, CancelOrders):
                replay.cancel_orders(ts_us=ts_us, order_ids=command.order_ids)
            else:
                replay.submit_orders(orders=(command,), market=market)
    assert replay.ledger.records == original.ledger.records
    assert replay.ledger.order_events == original.ledger.order_events
    assert replay.portfolio == original.portfolio


def test_fake_live_batch_boundaries_preserve_execution_and_account_facts() -> None:
    observations = (_observation(1, symbol="A"), _observation(1, symbol="B"),
                    _observation(2, symbol="A"), _observation(2, symbol="B"))
    offline = _execution()
    live = _execution()
    for execution, batches in ((offline, (observations,)),
                               (live, ((observations[0],), observations[1:3], (observations[3],)))):
        execution.submit_orders(orders=(_order(1, symbol="A"), _order(2, symbol="B")),
                                market=Market({"A": 10, "B": 10}))
        stream = chain.from_iterable(batches)
        for ts_us, simultaneous in groupby(stream, key=lambda item: item.time.available_ts_us):
            execution.advance(ts_us=ts_us, observations=tuple(simultaneous))
    assert live.portfolio == offline.portfolio
    assert live.ledger.records == offline.ledger.records
    assert live.ledger.commands == offline.ledger.commands
    assert live.ledger.order_events == offline.ledger.order_events


def test_same_time_input_must_be_merged_before_advancing() -> None:
    execution = _execution()
    execution.advance(ts_us=T + M, observations=(_observation(1),))
    with pytest.raises(ValueError, match="merge all same-time"):
        execution.advance(ts_us=T + M, observations=(_observation(1, symbol="B"),))


@pytest.mark.parametrize("reverse_observations", [False, True])
def test_same_interval_sale_cannot_fund_a_reference_price_gap(reverse_observations: bool) -> None:
    execution = _execution(cash=1000, reference=True)
    execution.portfolio.positions["A"] = 100
    execution.portfolio.avg_cost["A"] = Decimal("10")
    execution.submit_orders(orders=(_order(1, qty=100, side=Side.SELL, limit=None),
        _order(2, qty=100, symbol="B", limit=None)), market=Market({"A": 10, "B": 10}))
    observations = (_observation(1, volume=None), _observation(1, symbol="B", price="20", volume=None))
    if reverse_observations:
        observations = tuple(reversed(observations))
    fills = execution.advance(ts_us=T + M, observations=observations)
    assert [(fill.order_id, fill.qty) for fill in fills] == [(1, 100), (2, 50)]
    assert execution.portfolio.cash == 1000
    assert execution.portfolio.positions == {"B": 50}


def test_resource_priority_is_global_across_symbols_not_observation_order() -> None:
    execution = _execution(cash=300, reference=True)
    execution.submit_orders(orders=(_order(1, qty=10, symbol="B", limit=None),
        _order(2, qty=10, symbol="A", limit=None)), market=Market({"A": 10, "B": 10}))
    fills = execution.advance(ts_us=T + M, observations=(
        _observation(1, symbol="A", price="30", volume=None),
        _observation(1, symbol="B", price="30", volume=None)))
    assert [(fill.order_id, fill.qty) for fill in fills] == [(1, 6), (2, 3)]
    assert execution.portfolio.cash == 30
    assert execution.pending_orders[1].reserved_cash == 20
    assert execution.pending_orders[2].reserved_cash == 10


def test_daily_observation_uses_same_lifecycle_and_only_a_subsequent_complete_day() -> None:
    execution = _execution()
    next_open = DateTimeUtils.local_time_to_utc_epoch_us(time(9, 30), date(2026, 7, 6))
    next_close = DateTimeUtils.local_time_to_utc_epoch_us(time(15), date(2026, 7, 6))
    execution.submit_orders(orders=(_order(qty=100, expires=next_close),), market=Market({"A": 10}))
    today = ExecutionObservation("day-1", "A", BarTime("trading_day", 1, "2026-07-03",
        T - 5 * 60 * M, T + 30 * M, T + 30 * M), Decimal("10"), 100)
    assert execution.advance(ts_us=today.time.available_ts_us, observations=(today,)) == ()
    execution.portfolio.on_trading_day_start("2026-07-06")
    tomorrow = ExecutionObservation("day-2", "A", BarTime("trading_day", 1, "2026-07-06",
        next_open, next_close, next_close), Decimal("10"), 100)
    fills = execution.advance(ts_us=next_close, observations=(tomorrow,))
    assert fills[0].qty == 100
    assert not execution.pending_orders


def test_explicit_simulation_endpoint_keeps_open_order_and_position() -> None:
    execution = _execution()
    execution.submit_orders(orders=(_order(),), market=Market({"A": 10}))
    execution.advance(ts_us=T + M, observations=(_observation(1),))
    assert execution.portfolio.positions == {"A": 300}
    assert execution.portfolio.cash == 17_000
    assert execution.pending_orders[1].remaining_qty == 700


@pytest.mark.parametrize("missing", ["quote", "volume"])
def test_price_model_rejects_missing_required_input(missing: str) -> None:
    execution = _execution()
    if missing == "quote":
        with pytest.raises(ValueError, match="requires a limit_price"):
            execution.submit_orders(orders=(_order(limit=None),), market=Market({"A": 10}))
    else:
        execution.submit_orders(orders=(_order(),), market=Market({"A": 10}))
        with pytest.raises(ValueError, match="requires volume_qty"):
            execution.advance(ts_us=T + M, observations=(_observation(1, volume=None),))
    assert not execution.ledger.fills_by_id


def test_sell_waits_when_proceeds_and_cash_cannot_pay_the_minimum_fee() -> None:
    execution = _execution(cash=0, minimum=5)
    execution.portfolio.positions["A"] = 1
    execution.portfolio.avg_cost["A"] = Decimal("1")
    execution.submit_orders(orders=(_order(qty=1, side=Side.SELL, limit="1"),), market=Market({"A": 1}))
    assert execution.advance(ts_us=T + M, observations=(_observation(1, price="1", volume=1),)) == ()
    assert execution.ledger.order_events[-1].reason == "insufficient_cash_for_fees"
    assert execution.portfolio.cash == 0
    assert execution.portfolio.positions == {"A": 1}


def test_sell_fees_cannot_spend_another_orders_reserved_cash() -> None:
    execution = _execution(cash=1005, minimum=5)
    execution.portfolio.positions["A"] = 1
    execution.portfolio.avg_cost["A"] = Decimal("1")
    execution.submit_orders(orders=(_order(1, qty=1, side=Side.SELL, limit="1"),
        _order(2, qty=100, symbol="B")), market=Market({"A": 1, "B": 10}))
    assert execution.advance(ts_us=T + M, observations=(_observation(1, price="1", volume=1),)) == ()
    assert execution.portfolio.cash == 1005
    assert execution.pending_orders[2].reserved_cash == 1005
    assert execution.ledger.order_events[-1].reason == "insufficient_cash_for_fees"


def test_target_budget_includes_minimum_fee_before_the_first_fill() -> None:
    from unittest.mock import Mock
    from src.trading.backtest.market.data_view import PriceView
    account = PortfolioState(2005, 2005, current_day='2026-07-03')
    ledger = ExecutionLedger()
    rule = QuantityRules(100, 100, 1, 1, 1, Decimal('.01'), True)
    execution = FakeExecution(account, ledger, {}, SettlementEngine(AShareCostModel(.0003, 0, 0, 5)),
                              ReferenceVenue(), {'A': rule}, None)
    market = Mock(spec=PriceView)
    market.get_price.return_value = Decimal(10)
    execution.submit_targets(target=TargetEvent(1, {'A': 200}), cancel_order_ids=(),
        terms={'A': OrderTerms(1, 60_000_001)}, market=market)
    observation = ExecutionObservation('bar', 'A', BarTime('minute', 1, '2026-07-03', 1, 60_000_001, 60_000_001), Decimal(10), 200)
    fills = execution.advance(ts_us=60_000_001, observations=(observation,))
    assert len(fills) == 1
    assert account.cash == 0 and account.positions == {'A': 200}
    assert ledger.order_costs[1].total == 5 and len(ledger.fills_by_id) == 1
