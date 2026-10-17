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
            if self.symbols:
                symbols = self.symbols
            else:
                listings = self.access.historical_stock_listings(trade_date=session.trade_date)
                for symbol, list_date in listings.items():
                    if list_date is None:
                        unknown.add(symbol)
                    else:
                        unknown.discard(symbol)
                        if list_date <= session.trade_date:
                            retained.add(symbol)
                symbols = tuple(sorted(retained))
                context.unknown_listing_symbols_by_date[session.trade_date] = tuple(sorted(unknown - retained))
            context.symbols_by_date[session.trade_date] = symbols
        return context
