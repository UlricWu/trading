# filepath: src/trading/execution/fake.py
from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import cast

from src.trading.core.events import (
    CancelOrders, FillEvent, OrderEvent, OrderIntent, OrderStatus, OrderTerms,
    PendingOrder, Side, TargetEvent,
)
from src.trading.core.ledger import ExecutionLedger
from src.trading.execution.policies.constraints import (
    QuantityRules, QuoteRule, affordable_quantity, reconcile_target_orders,
)
from src.trading.execution.settlement import SettlementEngine
from src.trading.execution.venue.fake import LimitPriceVenue, ReferenceVenue
from src.trading.market.data_view import ExecutionObservation, MarketDataView
from src.trading.portfolio.state import PortfolioState


@dataclass(slots=True)
class FakeExecution:
    """Run-scoped pending-order lifecycle with one shared settlement path.

    The caller owns the account, ledger and pending container; this object is
    the sole pending-order writer. No method obtains data or writes external state.

    Example:
        execution = FakeExecution(account, ledger, pending_orders, settlement,
                                  venue, rules, quote_rule)
        execution.submit_orders(orders=(order,), market=market)
        fills = execution.advance(ts_us=minute_end_us, observations=(observation,))
    """

    portfolio: PortfolioState
    ledger: ExecutionLedger
    pending_orders: MutableMapping[int, PendingOrder]
    settlement: SettlementEngine
    venue: LimitPriceVenue | ReferenceVenue
    rules: Mapping[str, QuantityRules]
    quote_rule: QuoteRule | None
    _ts_us: int | None = field(default=None, init=False)
    _observations: dict[str, ExecutionObservation] = field(default_factory=dict, init=False)
    _last_interval_end: dict[str, int] = field(default_factory=dict, init=False)
    _last_advance_ts_us: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.rules = dict(self.rules)
        if self.pending_orders:
            raise ValueError("H03 requires an initial account with no pending orders")
        if not self.venue.requires_quote and self.quote_rule is not None:
            raise ValueError("reference execution does not use quote parameters")
        account = self.portfolio
        if account.current_day is None:
            raise ValueError("initial account must declare its current trade_date")
        if not account.cash.is_finite() or account.cash < 0:
            raise ValueError("initial account cash must be finite and non-negative")
        for symbol, qty in account.positions.items():
            if type(qty) is not int or qty < 0:
                raise ValueError("initial positions must be non-negative integers")
            if qty and symbol not in account.avg_cost:
                raise ValueError("initial position requires an explicit cost basis")
            if qty and (not account.avg_cost[symbol].is_finite() or account.avg_cost[symbol] < 0):
                raise ValueError("initial cost basis must be finite and non-negative")
        for symbol, bought in account.buy_qty_today.items():
            if type(bought) is not int or not 0 <= bought <= account.positions.get(symbol, 0):
                raise ValueError("initial bought-today quantity must be within the position")

    def submit_orders(
        self, *, orders: Sequence[OrderIntent], market: MarketDataView,
    ) -> tuple[OrderEvent, ...]:
        """Accept or reject each frozen quantity; submission never produces fills.

        Example:
            events = execution.submit_orders(orders=(order,), market=market)
        """
        events: list[OrderEvent] = []
        for order in orders:
            self._advance_time(order.ts_us)
            if order.order_id in self.ledger.orders_by_id:
                raise ValueError(f"duplicate order_id={order.order_id}")
            rule = self.rules[order.symbol]
            if self.venue.requires_quote and order.limit_price is None:
                raise ValueError("price-model execution requires a limit_price")
            if not self.venue.requires_quote and order.limit_price is not None:
                raise ValueError("reference orders require limit_price=None")
            self.ledger.orders_by_id[order.order_id] = order
            self.ledger.commands.append(order)
            self.ledger.record_order_submit(ts_us=order.ts_us, symbol=order.symbol,
                side=order.side.value, qty=order.qty, order_id=order.order_id)
            step = rule.buy_step_qty if order.side == Side.BUY else rule.sell_step_qty
            minimum = rule.buy_min_qty if order.side == Side.BUY else rule.sell_min_qty
            reason = ""
            reservation_price = order.limit_price
            if reservation_price is None:
                raw_price = market.get_price(order.symbol)
                if raw_price is None:
                    raise ValueError(f"missing funding reference price for {order.symbol}")
                reservation_price = Decimal(str(raw_price))
                if not reservation_price.is_finite() or reservation_price <= 0:
                    raise ValueError("funding reference price must be finite and positive")
            reserved_cash = Decimal("0")
            if order.qty < minimum or order.qty % step:
                reason = "quantity_rule"
            elif order.limit_price is not None and order.limit_price % rule.price_tick:
                reason = "price_tick"
            elif order.side == Side.BUY:
                turnover = reservation_price * order.qty
                cost = self.settlement.incremental_cost(order_id=order.order_id, side=order.side,
                    symbol=order.symbol, turnover=turnover, ledger=self.ledger)
                reserved_cash = turnover + cost.total
                available = self.portfolio.cash - sum(
                    (item.reserved_cash for item in self.pending_orders.values()), Decimal("0")
                )
                if reserved_cash > available:
                    reason = "insufficient_cash"
            else:
                sellable = self.portfolio.t1_sellable(order.symbol) if rule.enforce_t1 else self.portfolio.positions.get(order.symbol, 0)
                reserved = sum(item.reserved_sell_qty for item in self.pending_orders.values()
                               if item.intent.symbol == order.symbol)
                if order.qty > sellable - reserved:
                    reason = "insufficient_sellable"
            if reason:
                self.ledger.record_order_reject(ts_us=order.ts_us, symbol=order.symbol,
                    side=order.side.value, qty=order.qty, order_id=order.order_id, reason=reason)
                events.append(self._event(order.ts_us, order.order_id, OrderStatus.REJECTED,
                                          order.qty, reason))
            else:
                self.pending_orders[order.order_id] = PendingOrder(
                    order, order.qty, reserved_cash,
                    order.qty if order.side == Side.SELL else 0, reservation_price,
                )
                events.append(self._event(order.ts_us, order.order_id, OrderStatus.PENDING,
                                          order.qty, "accepted"))
        return tuple(events)

    def submit_targets(
        self, *, target: TargetEvent, cancel_order_ids: Sequence[int],
        terms: Mapping[str, OrderTerms], market: MarketDataView,
    ) -> tuple[OrderEvent, ...]:
        """Reconcile before cancelling, then create resource-constrained orders.

        Example:
            events = execution.submit_targets(target=target, cancel_order_ids=(),
                                               terms=terms, market=market)
        """
        self._advance_time(target.ts_us)
        if self.venue.requires_quote and self.quote_rule is None:
            raise ValueError("target-based price-model execution requires quote_rule")
        prices = {symbol: Decimal(str(price)) for symbol in target.targets
                  if (price := market.get_price(symbol)) is not None}
        plan = reconcile_target_orders(target=target, positions=self.portfolio.positions,
            pending=tuple(self.pending_orders.values()), cancel_order_ids=cancel_order_ids,
            terms=terms, prices=prices, rules=self.rules, quote=self.quote_rule,
            next_order_id=self.ledger.next_order_id)
        events = list(self.cancel_orders(ts_us=target.ts_us, order_ids=plan.cancel_order_ids))
        for order in plan.orders:
            rule = self.rules[order.symbol]
            step = rule.buy_step_qty if order.side == Side.BUY else rule.sell_step_qty
            qty = order.qty // step * step
            if order.side == Side.BUY:
                cash = self.portfolio.cash - sum(
                    (item.reserved_cash for item in self.pending_orders.values()), Decimal("0")
                )
                price = order.limit_price if order.limit_price is not None else prices[order.symbol]
                qty = affordable_quantity(order=order, price=price, maximum=qty, step=step,
                    cash=cash, settlement=self.settlement, ledger=self.ledger)
            else:
                sellable = self.portfolio.t1_sellable(order.symbol) if rule.enforce_t1 else self.portfolio.positions.get(order.symbol, 0)
                reserved = sum(item.reserved_sell_qty for item in self.pending_orders.values()
                               if item.intent.symbol == order.symbol)
                qty = min(qty, sellable - reserved) // step * step
            minimum = rule.buy_min_qty if order.side == Side.BUY else rule.sell_min_qty
            if qty >= minimum:
                events.extend(self.submit_orders(orders=(replace(order, qty=qty),), market=market))
        return tuple(events)

    def cancel_orders(self, *, ts_us: int, order_ids: Sequence[int]) -> tuple[OrderEvent, ...]:
        """Record cancellation commands, including requests for terminal orders.

        Example:
            events = execution.cancel_orders(ts_us=deadline, order_ids=(1,))
        """
        self._advance_time(ts_us)
        if not order_ids:
            return ()
        self.ledger.commands.append(CancelOrders(ts_us, tuple(order_ids)))
        events: list[OrderEvent] = []
        for order_id in order_ids:
            pending = self.pending_orders.pop(order_id, None)
            if pending is None:
                prior = next((event for event in reversed(self.ledger.order_events)
                              if event.order_id == order_id), None)
                if prior is None:
                    raise ValueError(f"unknown order_id={order_id}")
                events.append(self._event(ts_us, order_id, prior.status, prior.remaining_qty,
                                          "cancel_not_active"))
            else:
                events.append(self._event(ts_us, order_id, OrderStatus.CANCELLED,
                                          pending.remaining_qty, "cancelled"))
        return tuple(events)

    def advance(
        self, *, ts_us: int, observations: Sequence[ExecutionObservation],
    ) -> tuple[FillEvent, ...]:
        """Settle complete eligible intervals before expiring their remaining orders.

        Repeated identical observations are ignored; conflicts, late input and
        overlapping liquidity streams fail before this batch changes the account.

        Example:
            fills = execution.advance(ts_us=minute_end_us, observations=(observation,))
        """
        self._advance_time(ts_us)
        fresh: dict[str, ExecutionObservation] = {}
        last_end = dict(self._last_interval_end)
        for observation in observations:
            prior = self._observations.get(observation.observation_id) or fresh.get(observation.observation_id)
            if prior is not None:
                if prior != observation:
                    raise ValueError("conflicting observation_id")
                continue
            time = observation.time
            if time.available_ts_us != time.end_ts_us or time.available_ts_us != ts_us:
                raise ValueError("H03 requires complete observations available at the current interval end")
            if self.venue.requires_quote and observation.volume_qty is None:
                raise ValueError("price-model execution requires volume_qty")
            if time.start_ts_us < last_end.get(observation.symbol, time.start_ts_us):
                raise ValueError("overlapping execution observations would reuse liquidity")
            last_end[observation.symbol] = time.end_ts_us
            fresh[observation.observation_id] = observation

        if fresh and self._last_advance_ts_us == ts_us:
            raise ValueError("merge all same-time observations before advancing once")
        self._last_advance_ts_us = ts_us

        # A complete bar cannot establish that its sells preceded its buys.
        cash_before = self.portfolio.cash
        spent = Decimal("0")
        fills: list[FillEvent] = []
        by_symbol = {observation.symbol: observation for observation in fresh.values()}
        volumes = {observation.observation_id: observation.volume_qty for observation in fresh.values()}
        ordered = sorted(tuple(self.pending_orders.values()), key=lambda item: item.intent.active_ts_us)
        for item in ordered:
            order = item.intent
            observation = by_symbol.get(order.symbol)
            if observation is None or not (
                order.active_ts_us <= observation.time.start_ts_us
                and observation.time.end_ts_us <= order.expires_ts_us
            ):
                continue
            match = self.venue.match(order=order, observation=observation)
            status = OrderStatus.PARTIAL if item.remaining_qty < order.qty else OrderStatus.PENDING
            if not match.matches:
                self._event(ts_us, order.order_id, status, item.remaining_qty,
                            "price_not_matched", observation.observation_id, match.candidate_price)
                continue
            rule = self.rules[order.symbol]
            quantity = item.remaining_qty
            if self.venue.requires_quote:
                quantity = min(quantity, cast(int, volumes[observation.observation_id]))
            quantity = quantity // rule.fill_step_qty * rule.fill_step_qty
            reason = "volume_exhausted" if quantity == 0 else "resources_unavailable"
            if order.side == Side.BUY:
                reserved_elsewhere = sum((other.reserved_cash
                    for order_id, other in self.pending_orders.items() if order_id != order.order_id), Decimal("0"))
                budget = min(self.portfolio.cash, cash_before - spent) - reserved_elsewhere
                quantity = affordable_quantity(order=order, price=match.candidate_price,
                    maximum=quantity, step=rule.fill_step_qty, cash=budget,
                    settlement=self.settlement, ledger=self.ledger)
            else:
                sellable = self.portfolio.t1_sellable(order.symbol) if rule.enforce_t1 else self.portfolio.positions.get(order.symbol, 0)
                reserved_elsewhere = sum(other.reserved_sell_qty
                    for order_id, other in self.pending_orders.items()
                    if order_id != order.order_id and other.intent.symbol == order.symbol)
                quantity = min(quantity, sellable - reserved_elsewhere) // rule.fill_step_qty * rule.fill_step_qty
                fee = self.settlement.incremental_cost(order_id=order.order_id, side=order.side,
                    symbol=order.symbol, turnover=match.candidate_price * quantity, ledger=self.ledger)
                cash_available_for_fees = self.portfolio.cash - sum(
                    (pending.reserved_cash for pending in self.pending_orders.values()), Decimal("0")
                )
                if fee.total > cash_available_for_fees + match.candidate_price * quantity:
                    quantity = 0
                    reason = "insufficient_cash_for_fees"
            if quantity <= 0:
                self._event(ts_us, order.order_id, status, item.remaining_qty,
                            reason, observation.observation_id, match.candidate_price)
                continue
            fill = FillEvent(ts_us, order.symbol, order.side, quantity, match.candidate_price,
                order.order_id, f"{order.order_id}:{observation.observation_id}",
                observation.observation_id, observation.price)
            cash_at_fill = self.portfolio.cash
            if not self.settlement.settle_fill(fill=fill, portfolio=self.portfolio, ledger=self.ledger):
                continue
            if order.side == Side.BUY:
                spent += cash_at_fill - self.portfolio.cash
            remaining = item.remaining_qty - quantity
            if self.venue.requires_quote:
                volumes[observation.observation_id] = cast(int, volumes[observation.observation_id]) - quantity
            if remaining:
                reserved_cash = Decimal("0")
                if order.side == Side.BUY:
                    turnover = item.reservation_price * remaining
                    fee = self.settlement.incremental_cost(order_id=order.order_id, side=order.side,
                        symbol=order.symbol, turnover=turnover, ledger=self.ledger)
                    reserved_cash = turnover + fee.total
                    other_reservations = sum((other.reserved_cash
                        for pending_id, other in self.pending_orders.items()
                        if pending_id != order.order_id), Decimal("0"))
                    reserved_cash = min(reserved_cash, self.portfolio.cash - other_reservations)
                self.pending_orders[order.order_id] = PendingOrder(order, remaining, reserved_cash,
                    remaining if order.side == Side.SELL else 0, item.reservation_price)
            else:
                del self.pending_orders[order.order_id]
            self._event(ts_us, order.order_id, OrderStatus.PARTIAL if remaining else OrderStatus.FILLED,
                        remaining, "partial_fill" if remaining else "filled",
                        observation.observation_id, match.candidate_price)
            fills.append(fill)
        self._observations.update(fresh)
        self._last_interval_end = last_end
        for order_id, item in tuple(self.pending_orders.items()):
            if item.intent.expires_ts_us <= ts_us:
                last = next(event for event in reversed(self.ledger.order_events) if event.order_id == order_id)
                reason = "no_observation" if last.observation_id is None else last.reason
                self._event(ts_us, order_id, OrderStatus.EXPIRED, item.remaining_qty, f"expired:{reason}")
                del self.pending_orders[order_id]
        return tuple(fills)

    def _advance_time(self, ts_us: int) -> None:
        if self._ts_us is not None and ts_us < self._ts_us:
            raise ValueError("execution time must not regress")
        self._ts_us = ts_us

    def _event(
        self, ts_us: int, order_id: int, status: OrderStatus, remaining: int, reason: str,
        observation_id: str | None = None, candidate_price: Decimal | None = None,
    ) -> OrderEvent:
        event = OrderEvent(ts_us, order_id, status, remaining, reason, observation_id, candidate_price)
        self.ledger.order_events.append(event)
        return event
