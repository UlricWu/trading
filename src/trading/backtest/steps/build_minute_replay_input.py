# filepath: src/trading/backtest/steps/build_minute_replay_input.py
from __future__ import annotations

from dataclasses import dataclass

from src.access import Access
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.market.data_view import ExecutionObservation
from src.trading.backtest.market.preparation import confirmed_suspended_symbols, minute_replay_facts
from src.trading.backtest.market.replay import MarkObservation, ReplayMarketInput


@dataclass(frozen=True, slots=True)
class BuildMinuteReplayInputStep:
    """Read sparse stock minutes and official closes for a fixed symbol set.

    Example:
        context = BuildMinuteReplayInputStep(access, ("000001", "600000"), "v1").run(context)
    """

    access: Access
    symbols: tuple[str, ...]
    source_version: str

    def run(self, context: BacktestContext) -> BacktestContext:
        """Read both H02 minute objects per day; preserve missing-minute semantics.

        Example:
            context = step.run(context)
        """
        marks: list[MarkObservation] = []
        observations: list[ExecutionObservation] = []
        for session in context.sessions:
            bars = self.access.daily_bars(trade_date=session.trade_date)
            bars = bars.loc[bars["symbol"].isin(self.symbols)].copy()
            missing = set(self.symbols) - set(bars["symbol"])
            suspended = confirmed_suspended_symbols(
                missing_symbols=missing, suspensions=self.access.suspensions(trade_date=session.trade_date),
            ) if missing else frozenset()
            minutes = self.access.stock_trade_minutes(trade_date=session.trade_date)
            facts = minute_replay_facts(
                minutes=minutes, daily_bars=bars, session=session,
                symbols=self.symbols, source_version=self.source_version, suspended_symbols=suspended,
                missing_symbols=missing - suspended,
            )
            marks.extend(facts.marks)
            observations.extend(facts.observations)
        context.market_input = ReplayMarketInput(
            price_basis="raw", quantity_unit="shares", sessions=context.sessions,
            marks=tuple(marks), observations=tuple(observations),
        )
        return context
