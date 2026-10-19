# filepath: src/trading/backtest/market/preparation.py
"""Convert formal daily/minute facts into bounded raw-price replay inputs."""
from __future__ import annotations

from collections.abc import Collection, Sequence
from decimal import Decimal
from typing import NamedTuple

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from src.data_system.market_phase import MarketPhase
from src.data_system.security_status import SecurityStatus
from src.trading.backtest.market.data_view import ExecutionObservation
from src.trading.backtest.market.replay import MarketDataIssue, MarkObservation, ReplaySession
from src.trading.core.time import BarTime, US_PER_MINUTE


class ReplayFacts(NamedTuple):
    """Carry execution observations and their independently chosen valuation marks.

    Example:
        facts = ReplayFacts(marks=(daily_close,), observations=(observation,))
    """

    marks: tuple[MarkObservation, ...]
    observations: tuple[ExecutionObservation, ...]


def confirmed_suspended_symbols(
    *, missing_symbols: Collection[str], suspensions: pd.DataFrame,
    reviewed_statuses: Sequence[SecurityStatus] = (), observed_symbols: Collection[str] = (),
) -> frozenset[str]:
    """Return confirmed full-day suspensions, leaving ambiguous gaps unresolved.

    Example:
        suspended = confirmed_suspended_symbols(missing_symbols={"600000"},
            suspensions=events)
    """
    observed = set(observed_symbols)
    for status in reviewed_statuses:
        if (status.symbol in observed and (status.listing_status in {"pre_listing", "delisted"}
                or status.trading_status == "suspended")):
            raise ValueError(f"market data conflicts with reviewed security status: {status.symbol}@{status.trade_date}")
    reviewed_trading = {status.symbol for status in reviewed_statuses if status.trading_status is not None}
    events = suspensions.loc[suspensions["symbol"].isin(set(missing_symbols) - reviewed_trading)]
    full_day = events["suspend_type"].eq("S") & events["suspend_timing"].isna()
    confirmed = set(events.loc[full_day, "symbol"])
    conflicting = set(events.loc[~full_day, "symbol"])
    reviewed_suspended = {status.symbol for status in reviewed_statuses if status.trading_status == "suspended"}
    return frozenset((confirmed - conflicting) | (reviewed_suspended & set(missing_symbols)))


def _daily_close_mark(
    bars: pd.DataFrame, session: ReplaySession, suspended_symbols: frozenset[str],
    missing_symbols: Collection[str], unknown_listing_symbols: Collection[str],
) -> MarkObservation:
    return MarkObservation(
        ts_us=session.close_ts_us, trade_date=session.trade_date, kind="daily_close",
        prices={symbol: Decimal(str(close)) for symbol, close in
                bars.loc[:, ["symbol", "close"]].sort_values("symbol").itertuples(index=False, name=None)},
        suspended_symbols=suspended_symbols,
        data_issues=tuple(MarketDataIssue(
            ts_us=session.close_ts_us, trade_date=session.trade_date, symbol=symbol, reason="missing_daily_close",
        ) for symbol in sorted(missing_symbols)) + tuple(MarketDataIssue(
            ts_us=session.close_ts_us, trade_date=session.trade_date, symbol=symbol, reason="unknown_listing_status",
        ) for symbol in sorted(unknown_listing_symbols)),
    )


def daily_replay_facts(
    *, bars: pd.DataFrame, session: ReplaySession, source_version: str,
    suspended_symbols: frozenset[str] = frozenset(),
    missing_symbols: Collection[str] = (),
    unknown_listing_symbols: Collection[str] = (),
) -> ReplayFacts:
    """Use raw close and exact lot-to-share conversion for one complete account day.

    The interval starts at the declared account-day start, so intraday submissions
    cannot consume any part of that day's volume. Source tables remain unchanged.

    Example:
        facts = daily_replay_facts(bars=bars, session=session, source_version="v1")
    """
    close_mark = _daily_close_mark(bars, session, suspended_symbols, missing_symbols, unknown_listing_symbols)
    bar_time = BarTime(
        unit="trading_day", size=1, trade_date=session.trade_date,
        start_ts_us=session.start_ts_us, end_ts_us=session.close_ts_us,
        available_ts_us=session.close_ts_us,
    )
    observations: list[ExecutionObservation] = []
    for symbol, volume_lots in bars.loc[:, ["symbol", "vol"]].itertuples(index=False, name=None):
        volume_shares = Decimal(str(volume_lots)) * 100
        if (not volume_shares.is_finite() or volume_shares < 0
                or volume_shares != volume_shares.to_integral_value()):
            raise ValueError(f"daily_bar {session.trade_date}/{symbol}: vol * 100 must be whole non-negative shares")
        observations.append(ExecutionObservation(
            observation_id=f"daily_bar/{source_version}/{session.trade_date}/{symbol}"
                           f"/{bar_time.start_ts_us}/{bar_time.end_ts_us}",
            symbol=symbol, time=bar_time, price=close_mark.prices[symbol], volume_qty=int(volume_shares),
        ))
    return ReplayFacts((close_mark,), tuple(sorted(observations, key=lambda item: item.symbol)))


def minute_replay_facts(
    *, minutes: pa.Table, daily_bars: pd.DataFrame, session: ReplaySession,
    symbols: Sequence[str], source_version: str,
    suspended_symbols: frozenset[str] = frozenset(),
    missing_symbols: Collection[str] = (),
    unknown_listing_symbols: Collection[str] = (),
) -> ReplayFacts:
    """Map sparse continuous-trade minutes to VWAP fills, close marks and daily close.

    Whole source buckets retain their start/end, including an 11:30 bucket whose
    end is 11:31. Auctions are outside this scenario, not merged into continuous
    liquidity. Access owns source schema, keys, dates and ordering validation.

    Example:
        facts = minute_replay_facts(minutes=minutes, daily_bars=bars, session=session,
                                   symbols=("000001", "600000"), source_version="v1")
    """
    if suspended_symbols and pc.any(pc.is_in(
        minutes["symbol"], value_set=pa.array(sorted(suspended_symbols), type=pa.string()),
    )).as_py():
        raise ValueError(f"stock_trade_1m {session.trade_date}: trades conflict with full-day suspension")
    selected = minutes.filter(pc.and_(
        pc.is_in(minutes["symbol"], value_set=pa.array(symbols, type=pa.string())),
        pc.equal(minutes["phase"], int(MarketPhase.CONTINUOUS)),
    )).select(["symbol", "minute_start_ts_utc", "volume_sum", "notional_sum", "close"])
    valid_volume = pc.fill_null(pc.greater(selected["volume_sum"], 0), False)
    valid_notional = pc.fill_null(pc.and_(
        pc.is_finite(selected["notional_sum"]), pc.greater(selected["notional_sum"], 0),
    ), False)
    if pc.any(pc.invert(pc.and_(valid_volume, valid_notional))).as_py():
        raise ValueError(f"stock_trade_1m {session.trade_date}: volume_sum and notional_sum must be positive and finite")
    selected = selected.append_column("vwap", pc.divide(
        selected["notional_sum"], selected["volume_sum"],
    )).sort_by([("minute_start_ts_utc", "ascending"), ("symbol", "ascending")])
    observations: list[ExecutionObservation] = []
    prices_by_time: dict[int, dict[str, Decimal]] = {}
    for row in selected.to_pylist():
        start_us = row["minute_start_ts_utc"]
        end_us = start_us + US_PER_MINUTE
        symbol = row["symbol"]
        observations.append(ExecutionObservation(
            observation_id=f"stock_trade_1m/{source_version}/{session.trade_date}/{symbol}"
                           f"/{start_us}/{end_us}/phase={int(MarketPhase.CONTINUOUS)}",
            symbol=symbol,
            time=BarTime("minute", 1, session.trade_date, start_us, end_us, end_us),
            price=Decimal(str(row["vwap"])), volume_qty=row["volume_sum"],
        ))
        prices_by_time.setdefault(end_us, {})[symbol] = Decimal(str(row["close"]))
    marks = tuple(MarkObservation(ts_us=ts_us, trade_date=session.trade_date, prices=prices, kind="mark")
                  for ts_us, prices in prices_by_time.items())
    return ReplayFacts((*marks, _daily_close_mark(
        daily_bars, session, suspended_symbols, missing_symbols, unknown_listing_symbols,
    )), tuple(observations))
