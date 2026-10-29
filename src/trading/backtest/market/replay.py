# filepath: src/trading/backtest/market/replay.py
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StrictInt, model_validator

from src.trading.backtest.market.data_view import ExecutionObservation
from src.trading.core.time import TradingWindow
from src.utils.datetime_utils import DateTimeUtils


class ReplaySession(BaseModel):
    """An explicit account day and its actual trading-minute windows.

    Example:
        session = ReplaySession(trade_date="2026-07-03", start_ts_us=day_start,
                                close_ts_us=close_time, windows=(morning, afternoon))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    trade_date: str
    start_ts_us: StrictInt
    close_ts_us: StrictInt
    windows: tuple[TradingWindow, ...]

    @model_validator(mode="after")
    def _validate_windows(self) -> Self:
        DateTimeUtils.require_system_date(self.trade_date, field_name="trade_date")
        if not self.start_ts_us < self.close_ts_us or not self.windows:
            raise ValueError("session requires start < close and trading windows")
        end = self.start_ts_us
        for window in self.windows:
            if window.trade_date != self.trade_date or not end <= window.start_ts_us < window.end_ts_us <= self.close_ts_us:
                raise ValueError("session windows must be ordered, disjoint and within their account day")
            end = window.end_ts_us
        return self


class MarketDataIssue(BaseModel):
    """A dated unresolved listing status or missing close for confirmed coverage.

    Example:
        issue = MarketDataIssue(ts_us=close_us, trade_date="2026-09-15",
                                symbol="601238", reason="missing_daily_close")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    ts_us: StrictInt
    trade_date: str
    symbol: str
    reason: Literal["missing_daily_close", "unknown_listing_status"]


class MarkObservation(BaseModel):
    """Immutable raw valuation prices, separate from execution-volume observations.

    Example:
        mark = MarkObservation(ts_us=close_time, trade_date="2026-07-03",
                               prices={"600000": "10"}, kind="daily_close")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    ts_us: StrictInt
    trade_date: str
    prices: Annotated[
        Mapping[str, Decimal],
        PlainSerializer(lambda prices: dict(prices), return_type=dict[str, Decimal]),
    ]
    kind: Literal["mark", "daily_close"]
    suspended_symbols: frozenset[str] = frozenset()
    data_issues: tuple[MarketDataIssue, ...] = ()

    @model_validator(mode="after")
    def _validate_prices(self) -> Self:
        DateTimeUtils.require_system_date(self.trade_date, field_name="trade_date")
        if any(not price.is_finite() or price <= 0 for price in self.prices.values()):
            raise ValueError("raw marks must be finite and positive")
        if self.suspended_symbols and (self.kind != "daily_close" or self.suspended_symbols & self.prices.keys()):
            raise ValueError("suspended symbols belong only to a daily close without their prices")
        missing = {issue.symbol for issue in self.data_issues}
        if self.data_issues and (
            self.kind != "daily_close" or len(missing) != len(self.data_issues)
            or missing & (self.prices.keys() | self.suspended_symbols)
            or any(issue.ts_us != self.ts_us or issue.trade_date != self.trade_date for issue in self.data_issues)
        ):
            raise ValueError("data issues require distinct missing closes at the same date and timestamp")
        object.__setattr__(self, "prices", MappingProxyType(dict(self.prices)))
        return self


class ReplayMarketInput(BaseModel):
    """Share normalized facts and schedule queries across independent account replays.

    No source-unit inference is performed.

    Example:
        source = ReplayMarketInput(price_basis="raw", quantity_unit="shares",
            sessions=sessions, marks=marks, observations=observations)
        session = source.session_at(start_ts_us)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    price_basis: Literal["raw"]
    quantity_unit: Literal["shares"]
    sessions: tuple[ReplaySession, ...] = Field(min_length=1)
    marks: tuple[MarkObservation, ...]
    observations: tuple[ExecutionObservation, ...]

    @model_validator(mode="after")
    def _validate_timeline(self) -> Self:
        for prior, current in zip(self.sessions, self.sessions[1:]):
            if prior.trade_date >= current.trade_date or prior.close_ts_us >= current.start_ts_us:
                raise ValueError("sessions must be strictly ordered and disjoint")
        if any(a.ts_us > b.ts_us for a, b in zip(self.marks, self.marks[1:])):
            raise ValueError("marks must retain chronological order")
        if any(a.time.available_ts_us > b.time.available_ts_us for a, b in zip(self.observations, self.observations[1:])):
            raise ValueError("execution observations must retain availability order")
        sessions = {session.trade_date: session for session in self.sessions}
        seen_marks: set[tuple[int, str]] = set()
        for mark in self.marks:
            session = sessions.get(mark.trade_date)
            if session is None or not session.start_ts_us <= mark.ts_us <= session.close_ts_us:
                raise ValueError("mark must belong to its declared session")
            keys = {(mark.ts_us, symbol) for symbol in mark.prices}
            if keys & seen_marks:
                raise ValueError("each symbol requires one unambiguous mark at a timestamp")
            seen_marks.update(keys)
            if mark.kind == "daily_close" and mark.ts_us != session.close_ts_us:
                raise ValueError("daily_close marks must be available at the declared session close")
        for observation in self.observations:
            bar = observation.time
            if bar.available_ts_us != bar.end_ts_us:
                raise ValueError("delayed execution observations are outside the supported scenario")
            session = sessions.get(bar.trade_date)
            if session is None or not session.start_ts_us <= bar.start_ts_us < bar.end_ts_us <= session.close_ts_us:
                raise ValueError("execution observation must belong to its declared session")
        return self

    def session_at(self, ts_us: int) -> ReplaySession:
        """Resolve the account day from the declared schedule, independently of bars.

        Example:
            session = source.session_at(ts_us)
        """
        index = bisect_right(self.sessions, ts_us, key=lambda session: session.start_ts_us) - 1
        if index < 0 or ts_us > self.sessions[index].close_ts_us:
            raise ValueError("timestamp is outside the prepared account sessions")
        return self.sessions[index]

    def timestamps(self) -> tuple[int, ...]:
        """Return required schedule and input wakeups, including days without bars.

        Example:
            clock = ReplayClock.from_iterable(source.timestamps(), end_us=end_us)
        """
        return tuple(sorted({*(s.start_ts_us for s in self.sessions),
            *(s.close_ts_us for s in self.sessions), *(m.ts_us for m in self.marks),
            *(o.time.available_ts_us for o in self.observations)}))


@dataclass(frozen=True, slots=True)
class VisiblePrice:
    """A raw valuation price with the original observation time and kind.

    Example:
        price = VisiblePrice(Decimal("10"), close_us, "2026-07-17", "daily_close")
    """

    price: Decimal
    ts_us: int
    trade_date: str
    kind: Literal["mark", "daily_close", "execution", "corporate_action"]


class ReplayMarket:
    """Expose only facts delivered by the current timestamp and merge same-time batches.

    Example:
        market = ReplayMarket(market_input)
        market.on_time(ts_us)
        fills = execution_simulator.advance(ts_us=ts_us, observations=market.observations_at(ts_us))
    """

    def __init__(self, source: ReplayMarketInput, *, initial_marks: Sequence[MarkObservation] = ()) -> None:
        """Bind immutable normalized inputs to one run's visible price state.

        Example:
            market = ReplayMarket(source)
        """
        self.source = source
        self.daily_close_symbols: dict[int, set[str]] = {}
        self.suspended_close_symbols: dict[int, frozenset[str]] = {}
        self.daily_close_issues: dict[int, tuple[MarketDataIssue, ...]] = {}
        self._marks = tuple(sorted((*initial_marks, *source.marks), key=lambda mark: mark.ts_us))
        self._price_marks: dict[str, VisiblePrice] = {}
        self._distribution_references: dict[str, tuple[VisiblePrice | None, VisiblePrice]] = {}
        self._mark_index = 0
        self._observation_index = 0
        self._ts_us: int | None = None
        self._available: tuple[ExecutionObservation, ...] = ()

    def on_time(self, ts_us: int) -> None:
        """Advance visible marks; execution bars supply prices only after their end.

        Example:
            market.on_time(minute_end_us)
        """
        if self._ts_us is not None and ts_us < self._ts_us:
            raise ValueError("market time must not regress")
        self._ts_us = ts_us
        available: list[ExecutionObservation] = []
        while True:
            mark = (self._marks[self._mark_index]
                    if self._mark_index < len(self._marks) else None)
            observation = (self.source.observations[self._observation_index]
                           if self._observation_index < len(self.source.observations) else None)
            if observation is not None and observation.time.available_ts_us <= ts_us and (
                mark is None or observation.time.available_ts_us <= mark.ts_us
            ):
                self._price_marks[observation.symbol] = VisiblePrice(
                    observation.price, observation.time.available_ts_us, observation.time.trade_date, "execution",
                )
                if observation.time.available_ts_us == ts_us:
                    available.append(observation)
                self._observation_index += 1
            elif mark is not None and mark.ts_us <= ts_us:
                self._price_marks.update({symbol: VisiblePrice(price, mark.ts_us, mark.trade_date, mark.kind)
                                          for symbol, price in mark.prices.items()})
                if mark.kind == "daily_close":
                    self.daily_close_symbols.setdefault(mark.ts_us, set()).update(mark.prices)
                    self.suspended_close_symbols[mark.ts_us] = (
                        self.suspended_close_symbols.get(mark.ts_us, frozenset()) | mark.suspended_symbols
                    )
                    if mark.data_issues:
                        self.daily_close_issues[mark.ts_us] = (
                            self.daily_close_issues.get(mark.ts_us, ()) + mark.data_issues
                        )
                self._mark_index += 1
            else:
                break
        self._available = tuple(available)

    def get_price(self, symbol: str) -> Decimal | None:
        """Return the latest visible raw price, or an ordinary missing mark.

        Example:
            price = market.get_price("600000")
        """
        mark = self._price_marks.get(symbol)
        return None if mark is None else mark.price

    def apply_distribution_reference(
        self, *, action_id: str, symbol: str, ts_us: int, trade_date: str,
        reference_cash_per_share: Decimal | None, reference_shares_per_share: Decimal | None,
        reference_price: Decimal | None = None,
    ) -> None:
        """Replace a stale cum-dividend valuation basis without supplying trading volume.

        Example:
            market.apply_distribution_reference(action_id="distribution/600000", symbol="600000", ts_us=ex_start,
                trade_date="2026-09-03", reference_cash_per_share=Decimal("0.1"), reference_shares_per_share=Decimal(0))
        """
        prior = self._price_marks.get(symbol)
        if prior is not None and prior.ts_us >= ts_us:
            return
        if reference_price is not None:
            price = reference_price
        elif prior is None:
            return
        else:
            price = (prior.price - cast(Decimal, reference_cash_per_share)) / (1 + cast(Decimal, reference_shares_per_share))
        if price <= 0:
            raise ValueError(f"distribution reference price must be positive: {symbol}/{trade_date}")
        applied = VisiblePrice(price, ts_us, trade_date, "corporate_action")
        self._price_marks[symbol] = applied
        self._distribution_references[action_id] = (prior, applied)

    def revise_distribution_reference(
        self, *, action_id: str, symbol: str, ts_us: int, ex_ts_us: int, trade_date: str,
        reference_cash_per_share: Decimal | None, reference_shares_per_share: Decimal | None,
        reference_price: Decimal | None = None,
    ) -> bool:
        """Revise this event's mark using a published price or its original formula basis.

        Example:
            changed = market.revise_distribution_reference(action_id="distribution/600000",
                symbol="600000", ts_us=publication_us, ex_ts_us=ex_start, trade_date="2026-09-03",
                reference_cash_per_share=Decimal("0.04"),
                reference_shares_per_share=Decimal(0))
        """
        reference = self._distribution_references.get(action_id)
        if reference is None:
            basis = self._price_marks.get(symbol)
            if basis is not None and basis.ts_us >= ex_ts_us:
                return False
            applied = None
        else:
            basis, applied = reference
            if self._price_marks.get(symbol) is not applied:
                return False
        if reference_price is not None:
            price = reference_price
        else:
            if basis is None:
                raise ValueError("formula revision requires an original cum-distribution price")
            price = (basis.price - cast(Decimal, reference_cash_per_share)) / (1 + cast(Decimal, reference_shares_per_share))
        if price <= 0:
            raise ValueError(f"distribution reference price must be positive: {symbol}/{trade_date}")
        if applied is not None and price == applied.price:
            return False
        revised = VisiblePrice(price, ts_us, trade_date if applied is None else applied.trade_date, "corporate_action")
        self._price_marks[symbol] = revised
        self._distribution_references[action_id] = (basis, revised)
        return True

    @property
    def prices(self) -> Mapping[str, Decimal]:
        """Return an independent visible-price snapshot.

        Example:
            visible = market.prices
        """
        return {symbol: mark.price for symbol, mark in self._price_marks.items()}

    @property
    def price_marks(self) -> Mapping[str, VisiblePrice]:
        """Return visible price origins without changing their source timestamps.

        Example:
            mark = market.price_marks["600000"]
        """
        return dict(self._price_marks)

    def observations_at(self, ts_us: int) -> tuple[ExecutionObservation, ...]:
        """Return the complete same-time batch after advancing the view.

        Example:
            observations = market.observations_at(ts_us)
        """
        if ts_us != self._ts_us:
            raise ValueError("advance the market before consuming observations")
        return self._available
