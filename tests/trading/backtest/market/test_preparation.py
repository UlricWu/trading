# filepath: tests/trading/backtest/market/test_preparation.py
from __future__ import annotations

from datetime import date, time

import pandas as pd
import pyarrow as pa
import pytest

from src.trading.backtest.market.preparation import confirmed_suspended_symbols, daily_replay_facts, minute_replay_facts
from src.trading.backtest.market.replay import ReplaySession
from src.trading.core.time import TradingWindow, US_PER_MINUTE
from src.utils.datetime_utils import DateTimeUtils


@pytest.fixture
def session() -> ReplaySession:
    start, morning_open, morning_close, afternoon_open, close = (
        DateTimeUtils.local_time_to_utc_epoch_us(clock, date(2026, 7, 20))
        for clock in (time(0), time(9, 30), time(11, 30), time(13), time(15))
    )
    return ReplaySession(trade_date="2026-07-20", start_ts_us=start, close_ts_us=close,
                         windows=(TradingWindow("2026-07-20", morning_open, morning_close),
                                  TradingWindow("2026-07-20", afternoon_open, close)))


@pytest.mark.parametrize("notional", [float("nan"), float("inf"), -1.0, 0.0, None])
def test_minute_converter_rejects_invalid_vwap_inputs(session: ReplaySession, notional: float | None) -> None:
    minutes = pa.table({"symbol": ["600000"], "phase": [2],
                        "minute_start_ts_utc": [session.windows[0].start_ts_us],
                        "volume_sum": [100], "notional_sum": pa.array([notional], type=pa.float64()),
                        "close": [10.0]})
    with pytest.raises(ValueError, match="notional_sum must be positive and finite"):
        minute_replay_facts(minutes=minutes, daily_bars=pd.DataFrame({"symbol": ["600000"], "close": [11.0]}),
                            session=session, symbols=("600000",), source_version="v1")


def test_converters_preserve_source_objects_and_keep_selection_outside_execution(session: ReplaySession) -> None:
    bars = pd.DataFrame({"symbol": ["600000"], "close": [11.0], "vol": [101]})
    before = bars.copy(deep=True)
    minutes = pa.table({"symbol": ["600000", "600000", "000001"], "phase": [0, 2, 2],
                        "minute_start_ts_utc": [session.windows[0].start_ts_us] * 3,
                        "volume_sum": [100, 100, 100], "notional_sum": [3000.0, 1000.0, 2000.0],
                        "close": [30.0, 11.0, 20.0]})
    frozen = minutes.to_pydict()
    daily = daily_replay_facts(bars=bars, session=session, source_version="v1")
    facts = minute_replay_facts(minutes=minutes, daily_bars=bars, session=session,
                               symbols=("600000",), source_version="v1")
    pd.testing.assert_frame_equal(bars, before)
    assert minutes.to_pydict() == frozen
    assert daily.observations[0].volume_qty == 101
    assert len(facts.observations) == 1
    assert facts.observations[0].price == 10
    assert facts.observations[0].time.end_ts_us == session.windows[0].start_ts_us + US_PER_MINUTE
    assert facts.marks[0].prices["600000"] == 11
    assert facts.marks[-1].kind == "daily_close"


@pytest.mark.parametrize("types,timings", [([], []), (["R"], [None]), (["S", "R"], [None, None]),
                                        (["S"], ["09:30-10:00"])])
def test_ambiguous_events_do_not_confirm_full_day_suspension(types: list[str], timings: list[str | None]) -> None:
    events = pd.DataFrame({"symbol": ["600000"] * len(types), "suspend_type": types, "suspend_timing": timings})
    assert confirmed_suspended_symbols(missing_symbols={"600000"}, suspensions=events) == frozenset()


def test_confirmed_suspension_creates_no_price_or_liquidity(session: ReplaySession) -> None:
    events = pd.DataFrame({"symbol": ["600000", "000001"], "suspend_type": ["S", "R"], "suspend_timing": [None, None]})
    suspended = confirmed_suspended_symbols(missing_symbols={"600000"}, suspensions=events)
    bars = pd.DataFrame({"symbol": pd.Series(dtype="str"), "close": pd.Series(dtype=float), "vol": pd.Series(dtype=float)})
    facts = daily_replay_facts(bars=bars, session=session, source_version="v1", suspended_symbols=suspended)
    assert facts.observations == ()
    assert facts.marks[0].prices == {}
    assert facts.marks[0].suspended_symbols == {"600000"}


@pytest.mark.parametrize("phase", [0, 2])
def test_any_real_trade_conflicts_with_claimed_full_day_suspension(session: ReplaySession, phase: int) -> None:
    minutes = pa.table({"symbol": ["600000"], "phase": [phase]})
    with pytest.raises(ValueError, match="trades conflict with full-day suspension"):
        minute_replay_facts(minutes=minutes, daily_bars=pd.DataFrame(), session=session,
                            symbols=("600000",), source_version="v1", suspended_symbols=frozenset({"600000"}))
