# filepath: src/trading/backtest/market/replay.py
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

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


class MarkObservation(BaseModel):
    """Visible raw valuation prices, separate from execution-volume observations.

    Example:
        mark = MarkObservation(ts_us=close_time, trade_date="2026-07-03",
                               prices={"600000": "10"}, kind="daily_close")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    ts_us: StrictInt
    trade_date: str
    prices: dict[str, Decimal]
    kind: Literal["mark", "daily_close"]

    @model_validator(mode="after")
    def _validate_prices(self) -> Self:
        DateTimeUtils.require_system_date(self.trade_date, field_name="trade_date")
        if any(not price.is_finite() or price <= 0 for price in self.prices.values()):
            raise ValueError("raw marks must be finite and positive")
        return self


class ReplayMarketInput(BaseModel):
    """Share normalized facts and schedule queries across independent account replays.

    No source-unit inference is performed.

    Example:
        source = ReplayMarketInput.model_validate_json(Path("/data/market.json").read_bytes(), strict=True)
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
            if mark.kind == "daily_close" and (
                mark.trade_date not in sessions or mark.ts_us != sessions[mark.trade_date].close_ts_us
            ):
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


class ReplayMarket:
    """Expose only facts delivered by the current timestamp and merge same-time batches.

    Example:
        market = ReplayMarket(market_input)
        market.on_time(ts_us)
        fills = execution.advance(ts_us=ts_us, observations=market.observations_at(ts_us))
    """

    def __init__(self, source: ReplayMarketInput) -> None:
        """Bind immutable normalized inputs to one run's visible price state.

        Example:
            market = ReplayMarket(source)
        """
        self.source = source
        self.daily_close_symbols: dict[int, set[str]] = {}
        for mark in source.marks:
            if mark.kind == "daily_close":
                self.daily_close_symbols.setdefault(mark.ts_us, set()).update(mark.prices)
        self._prices: dict[str, Decimal] = {}
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
            mark = (self.source.marks[self._mark_index]
                    if self._mark_index < len(self.source.marks) else None)
            observation = (self.source.observations[self._observation_index]
                           if self._observation_index < len(self.source.observations) else None)
            if observation is not None and observation.time.available_ts_us <= ts_us and (
                mark is None or observation.time.available_ts_us <= mark.ts_us
            ):
                self._prices[observation.symbol] = observation.price
                if observation.time.available_ts_us == ts_us:
                    available.append(observation)
                self._observation_index += 1
            elif mark is not None and mark.ts_us <= ts_us:
                self._prices.update(mark.prices)
                self._mark_index += 1
            else:
                break
        self._available = tuple(available)

    def get_price(self, symbol: str) -> Decimal | None:
        """Return the latest visible raw price, or an ordinary missing mark.

        Example:
            price = market.get_price("600000")
        """
        return self._prices.get(symbol)

    @property
    def prices(self) -> Mapping[str, Decimal]:
        """Return an independent visible-price snapshot.

        Example:
            visible = market.prices
        """
        return dict(self._prices)

    def observations_at(self, ts_us: int) -> tuple[ExecutionObservation, ...]:
        """Return the complete same-time batch after advancing the view.

        Example:
            observations = market.observations_at(ts_us)
        """
        if ts_us != self._ts_us:
            raise ValueError("advance the market before consuming observations")
        return self._available
