# filepath: src/trading/portfolio/constructors/fixed_holding.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from types import MappingProxyType

from src.trading.core.events import PendingOrder, Side, TargetEvent
from src.trading.core.time import trading_day_exit_ts
from src.trading.orders.constraints import QuantityRules
from src.trading.portfolio.state import PortfolioState
from src.utils.datetime_utils import DateTimeUtils


@dataclass(slots=True)
class FixedHoldingConstructor:
    """Own fixed selection and exit plans independently of signal and execution cadence.

    Example:
        strategy = FixedHoldingConstructor(2, time(14, 30), time(14), dates, rules, {})
        target = strategy.targets(ts_us=decision, trade_date=day, scores=scores,
                                  state=account, prices=prices, pending_orders=())
    """

    max_positions: int
    decision_time: time
    exit_time: time
    open_dates: Sequence[str]
    rules: Mapping[str, QuantityRules]
    exit_due: dict[str, int]
    _next_wakeups: set[int] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self.open_dates = tuple(self.open_dates)
        self.rules = MappingProxyType(dict(self.rules))
        self.exit_due = dict(self.exit_due)

    def targets(
        self, *, ts_us: int, trade_date: str, scores: Mapping[str, float] | None,
        state: PortfolioState, prices: Mapping[str, Decimal], pending_orders: Sequence[PendingOrder],
    ) -> TargetEvent | None:
        """Keep carryover slots, never spend unfilled sells, and retry exits on open days.

        Example:
            target = strategy.targets(ts_us=exit_due, trade_date=day, scores=None,
                                      state=account, prices=prices, pending_orders=pending)
        """
        self._next_wakeups.clear()
        committed = dict(state.positions)
        for pending in pending_orders:
            order = pending.intent
            committed[order.symbol] = committed.get(order.symbol, 0) + (
                pending.remaining_qty if order.side == Side.BUY else -pending.remaining_qty)
        due = {symbol for symbol, deadline in self.exit_due.items() if deadline <= ts_us}
        active_due = {symbol for symbol in due if state.positions.get(symbol, 0) or committed.get(symbol, 0)}
        for symbol in due - active_due:
            del self.exit_due[symbol]
        targets = dict(committed)
        for symbol in active_due:
            targets[symbol] = 0
        # A failed exit remains a holding and consumes a slot at the next entry.
        if active_due:
            index = self.open_dates.index(trade_date) + 1
            if index < len(self.open_dates):
                retry = DateTimeUtils.local_time_to_utc_epoch_us(
                    self.exit_time, date.fromisoformat(self.open_dates[index]))
                for symbol in active_due:
                    self.exit_due[symbol] = retry
                self._next_wakeups.add(retry)
            else:
                for symbol in active_due:
                    del self.exit_due[symbol]
        if scores is not None:
            expected = DateTimeUtils.local_time_to_utc_epoch_us(self.decision_time, date.fromisoformat(trade_date))
            if ts_us != expected:
                raise ValueError("fixed_holding requires signals at its explicit decision_time")
            occupied = {s for s, qty in state.positions.items() if qty > 0} | {
                pending.intent.symbol for pending in pending_orders if pending.intent.side == Side.BUY}
            slots = max(0, self.max_positions - len(occupied))
            selected = sorted((s for s in scores if s not in occupied and s not in active_due),
                              key=lambda symbol: (-scores[symbol], symbol))[:slots]
            if selected:
                deadline = trading_day_exit_ts(entry_trade_date=trade_date, holding_days=1,
                    exit_time=self.exit_time, open_dates=self.open_dates, timezone=DateTimeUtils.MARKET_TIMEZONE)
                available_cash = state.cash - sum((order.reserved_cash for order in pending_orders), Decimal(0))
                per_slot = available_cash / slots
                for symbol in selected:
                    rule = self.rules[symbol]
                    qty = int(per_slot // (prices[symbol] * rule.buy_step_qty)) * rule.buy_step_qty
                    if qty >= rule.buy_min_qty:
                        targets[symbol] = qty
                        self.exit_due[symbol] = deadline
                self._next_wakeups.add(deadline)
        if scores is None and not active_due:
            return None
        return TargetEvent(ts_us, targets)

    def wakeups(self) -> tuple[int, ...]:
        """Return future plan times discovered by the last strategy decision.

        Example:
            context.requested_wakeup_ts_us.update(strategy.wakeups())
        """
        return tuple(sorted(self._next_wakeups))
