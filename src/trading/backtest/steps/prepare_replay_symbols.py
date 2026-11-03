# filepath: src/trading/backtest/steps/prepare_replay_symbols.py
from __future__ import annotations

from dataclasses import dataclass

from src.access import Access
from src.trading.backtest.context import BacktestContext


@dataclass(frozen=True, slots=True)
class PrepareReplaySymbolsStep:
    """Resolve market coverage without using strategy eligibility or future members.

    Example:
        context = PrepareReplaySymbolsStep(access, symbols=(), held_symbols=()).run(context)
    """

    access: Access
    symbols: tuple[str, ...]
    held_symbols: tuple[str, ...]

    def run(self, context: BacktestContext) -> BacktestContext:
        """Keep confirmed coverage and unresolved listing candidates separately.

        A raw list entry alone does not prove listing. Retaining confirmed members
        preserves holdings and pending orders across null dates and list exits.

        Example:
            context = step.run(context)
        """
        retained = set(self.held_symbols)
        unknown: set[str] = set()
        for session in context.sessions:
            statuses = self.access.security_status(trade_date=session.trade_date)
            if statuses:
                context.security_status_by_date[session.trade_date] = statuses
            pre_listing = {status.symbol for status in statuses if status.listing_status == "pre_listing"}
            if pre_listing & retained:
                raise ValueError(f"reviewed pre-listing status conflicts with prior holdings or listing: {session.trade_date}")
            if self.symbols:
                symbols = tuple(symbol for symbol in self.symbols if symbol not in pre_listing)
            else:
                listings = self.access.historical_stock_listings(trade_date=session.trade_date)
                for symbol, list_date in listings.items():
                    if symbol in pre_listing:
                        unknown.discard(symbol)
                        continue
                    if list_date is None:
                        unknown.add(symbol)
                    else:
                        unknown.discard(symbol)
                        if list_date <= session.trade_date:
                            retained.add(symbol)
                for status in statuses:
                    if status.listing_status is not None:
                        unknown.discard(status.symbol)
                    if status.listing_status == "listed" and status.symbol in listings:
                        retained.add(status.symbol)
                symbols = tuple(sorted(retained))
                context.unknown_listing_symbols_by_date[session.trade_date] = tuple(sorted(unknown - retained))
            context.symbols_by_date[session.trade_date] = symbols
        return context
