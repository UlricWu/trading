# filepath: src/trading/portfolio/state.py
from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from decimal import Decimal

from src.trading.core.events import FillEvent, Side
from src.trading.portfolio.corporate_actions import DistributionEntitlement


@dataclass(frozen=True, slots=True)
class FillCost:
    """Exact incremental charges.

    Example:
        cost = FillCost(Decimal("5"), Decimal("0"), Decimal("0"))
        charge = cost.total
    """

    commission: Decimal
    stamp_tax: Decimal
    transfer_fee: Decimal

    @property
    def total(self) -> Decimal:
        """Return total charges.

        Example:
            charge = cost.total
        """
        return self.commission + self.stamp_tax + self.transfer_fee


@dataclass(slots=True)
class PortfolioState:
    """One run's account, mutated only by settlement and explicit day changes.

    Example:
        account = PortfolioState(Decimal("20000"), Decimal("20000"),
                                 current_day="2026-07-03")
        account.on_trading_day_start("2026-07-03")
    """

    initial_cash: Decimal
    cash: Decimal
    positions: dict[str, int] = field(default_factory=dict)
    avg_cost: dict[str, Decimal] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0")
    buy_qty_today: dict[str, int] = field(default_factory=dict)
    current_day: str | None = None
    distribution_entitlements: dict[str, DistributionEntitlement] = field(default_factory=dict)
    registration_holdings: dict[str, dict[str, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.initial_cash = Decimal(str(self.initial_cash))
        self.cash = Decimal(str(self.cash))
        self.realized_pnl = Decimal(str(self.realized_pnl))
        self.positions = dict(self.positions)
        self.avg_cost = {symbol: Decimal(str(cost)) for symbol, cost in self.avg_cost.items()}
        self.buy_qty_today = dict(self.buy_qty_today)
        self.distribution_entitlements = dict(self.distribution_entitlements)
        self.registration_holdings = {day: dict(positions) for day, positions in self.registration_holdings.items()}

    def on_trading_day_start(self, trade_date: str) -> None:
        """Unlock prior-day buys once, preserving initial snapshot restrictions.

        Example:
            account.on_trading_day_start("2026-07-06")
        """
        if self.current_day == trade_date:
            return
        if self.current_day is not None:
            if trade_date < self.current_day:
                raise ValueError("account trade_date must not regress")
            self.buy_qty_today.clear()
        self.current_day = trade_date

    def t1_sellable(self, symbol: str) -> int:
        """Return shares outside both same-day purchases and unlisted distributions.

        Example:
            sellable = account.t1_sellable("600000")
        """
        return self.positions.get(symbol, 0) - self.buy_qty_today.get(symbol, 0) - self.unlisted_shares(symbol)

    def unlisted_shares(self, symbol: str) -> int:
        """Return allocated stock that cannot trade yet, regardless of the T+1 setting.

        Example:
            locked = account.unlisted_shares("600000")
        """
        return sum(item.shares_qty for item in self.distribution_entitlements.values()
                   if item.distribution.symbol == symbol and item.recognized and not item.shares_released
                   and (item.distribution.kind == "distribution" or item.shares_credited))

    @property
    def dividend_receivable(self) -> Decimal:
        """Value recognized cash rights without making them spendable.

        Example:
            equity = account.cash + market_value + account.dividend_receivable
        """
        return sum((item.cash_amount for item in self.distribution_entitlements.values()
                    if item.recognized and not item.cash_paid), Decimal(0))

    def apply_fill(
        self, fill: FillEvent, *, cost: FillCost,
        meta_out: MutableMapping[str, object],
    ) -> None:
        """Apply the exact fill; settlement owns identity and all ledger writes.

        Example:
            account.apply_fill(fill, cost=cost, meta_out={})
        """
        quantity = self.positions.get(fill.symbol, 0)
        if fill.side == Side.SELL and fill.qty > quantity:
            raise ValueError("fill exceeds account position")
        prior_cost = self.avg_cost[fill.symbol] if quantity else Decimal("0")
        turnover = fill.price * fill.qty
        cash_after = self.cash + (turnover if fill.side == Side.SELL else -turnover) - cost.total
        if cash_after < 0:
            raise ValueError("fill would overdraw account cash")
        meta_out.update(pos_before=quantity, avg_cost_before=float(prior_cost))
        if fill.side == Side.BUY:
            self.avg_cost[fill.symbol] = (prior_cost * quantity + turnover) / (quantity + fill.qty)
            self.positions[fill.symbol] = quantity + fill.qty
            self.buy_qty_today[fill.symbol] = self.buy_qty_today.get(fill.symbol, 0) + fill.qty
            meta_out.update(pos_after=quantity + fill.qty, avg_cost_after=float(self.avg_cost[fill.symbol]))
        else:
            pnl = (fill.price - prior_cost) * fill.qty
            self.realized_pnl += pnl
            remaining = quantity - fill.qty
            if remaining:
                self.positions[fill.symbol] = remaining
                if fill.symbol in self.buy_qty_today:
                    self.buy_qty_today[fill.symbol] = min(
                        self.buy_qty_today[fill.symbol], remaining - self.unlisted_shares(fill.symbol))
            else:
                self.positions.pop(fill.symbol)
                self.avg_cost.pop(fill.symbol)
                self.buy_qty_today.pop(fill.symbol, None)
            meta_out.update(pos_after=remaining, avg_cost=float(prior_cost),
                            pnl_gross=float(pnl), pnl_net=float(pnl - cost.total),
                            sell_qty=fill.qty)
        self.cash = cash_after
        self.realized_pnl -= cost.total
        meta_out.update(cash_after=float(self.cash), realized_pnl_after=float(self.realized_pnl))
