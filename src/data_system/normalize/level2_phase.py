# filepath: src/data_system/normalize/level2_phase.py
"""Resolve effective-dated Level-2 trade phases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Literal

import pyarrow as pa
import pyarrow.compute as pc

from src.data_system.market_phase import MarketPhase
from src.utils.datetime_utils import DateTimeUtils


@dataclass(frozen=True, slots=True)
class _PhaseInterval:
    """One local-time interval in a Level-2 trade phase rule."""

    start_local_time: time
    end_local_time: time
    end_inclusive: bool
    phase: MarketPhase


@dataclass(frozen=True, slots=True)
class _PhaseRule:
    """Effective-dated trade phase rule for one exchange and security scope."""

    exchange: str
    effective_from: date
    effective_to: date | None
    intervals: tuple[_PhaseInterval, ...]
    security_types: tuple[str, ...]


# Source: docs/data/market_phase.md, section "Level-2 正成交窗口".
_EARLIEST_SUPPORTED_DATE = date(1900, 1, 1)
_SSE_CLOSE_CALL_EFFECTIVE_DATE = date(2018, 8, 20)
_SSE_FUND_CLOSE_CALL_EFFECTIVE_DATE = date(2026, 7, 6)
_SZ_CONVERTIBLE_BOND_RESUME_CALL_EFFECTIVE_DATE = date(2020, 6, 8)

_TRADE_STOCK_PM = (
    _PhaseInterval(time(13, 0), time(14, 57), False, MarketPhase.CONTINUOUS),
)

_TRADE_FUND_ETF_SH_PM = (
    _PhaseInterval(time(13, 0), time(15, 0), False, MarketPhase.CONTINUOUS),
)

_TRADE_BOND_SH_PM = (
    _PhaseInterval(time(13, 0), time(15, 30), False, MarketPhase.CONTINUOUS),
)

_TRADE_REGULAR_AM = (
    _PhaseInterval(time(9, 30), time(11, 30), True, MarketPhase.CONTINUOUS),
)

_SZ_OPEN_AND_CLOSE_CALL = (
    _PhaseInterval(time(9, 25), time(9, 25, 1), False, MarketPhase.AUCTION),
    _PhaseInterval(time(15, 0), time(15, 0, 1), False, MarketPhase.AUCTION),
)

_SZ_CONVERTIBLE_BOND_RESUME_CALL = (
    _PhaseInterval(time(14, 57), time(14, 57, 1), False, MarketPhase.AUCTION),
)

_DEFAULT_A_SHARE_TRADE_PHASE_RULES: tuple[_PhaseRule, ...] = (
    _PhaseRule(
        exchange="sh",
        effective_from=_SSE_CLOSE_CALL_EFFECTIVE_DATE,
        effective_to=None,
        intervals=(
            _PhaseInterval(
                time(9, 25),
                time(9, 25, 2),
                False,
                MarketPhase.AUCTION,
            ),
            *_TRADE_REGULAR_AM,
            *_TRADE_STOCK_PM,
            _PhaseInterval(
                time(15, 0),
                time(15, 0, 3),
                False,
                MarketPhase.AUCTION,
            ),
        ),
        security_types=("stock", "cdr"),
    ),
    _PhaseRule(
        exchange="sh",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=(
            _PhaseInterval(
                time(9, 25),
                time(9, 25, 1),
                False,
                MarketPhase.AUCTION,
            ),
            *_TRADE_REGULAR_AM,
            *_TRADE_STOCK_PM,
            _PhaseInterval(
                time(15, 0),
                time(15, 0, 2),
                False,
                MarketPhase.AUCTION,
            ),
        ),
        security_types=("b_share",),
    ),
    _PhaseRule(
        exchange="sz",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=_SZ_OPEN_AND_CLOSE_CALL + _TRADE_REGULAR_AM + _TRADE_STOCK_PM,
        security_types=("stock", "fund", "etf", "bond"),
    ),
    _PhaseRule(
        exchange="sz",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=_SZ_OPEN_AND_CLOSE_CALL + _TRADE_REGULAR_AM + _TRADE_STOCK_PM,
        security_types=("b_share",),
    ),
    _PhaseRule(
        exchange="sz",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=_SZ_CONVERTIBLE_BOND_RESUME_CALL_EFFECTIVE_DATE
        - timedelta(days=1),
        intervals=_SZ_OPEN_AND_CLOSE_CALL + _TRADE_REGULAR_AM + _TRADE_STOCK_PM,
        security_types=("convertible_bond",),
    ),
    _PhaseRule(
        exchange="sz",
        effective_from=_SZ_CONVERTIBLE_BOND_RESUME_CALL_EFFECTIVE_DATE,
        effective_to=None,
        intervals=(
            _SZ_OPEN_AND_CLOSE_CALL
            + _TRADE_REGULAR_AM
            + _TRADE_STOCK_PM
            + _SZ_CONVERTIBLE_BOND_RESUME_CALL
        ),
        security_types=("convertible_bond",),
    ),
    _PhaseRule(
        exchange="sh",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=_SSE_FUND_CLOSE_CALL_EFFECTIVE_DATE - timedelta(days=1),
        intervals=(
            _PhaseInterval(
                time(9, 25),
                time(9, 25, 2),
                False,
                MarketPhase.AUCTION,
            ),
            *_TRADE_REGULAR_AM,
            *_TRADE_FUND_ETF_SH_PM,
        ),
        security_types=("fund", "etf"),
    ),
    _PhaseRule(
        exchange="sh",
        effective_from=_SSE_FUND_CLOSE_CALL_EFFECTIVE_DATE,
        effective_to=None,
        intervals=(
            _PhaseInterval(
                time(9, 25),
                time(9, 25, 2),
                False,
                MarketPhase.AUCTION,
            ),
            *_TRADE_REGULAR_AM,
            *_TRADE_STOCK_PM,
            _PhaseInterval(
                time(15, 0),
                time(15, 0, 3),
                False,
                MarketPhase.AUCTION,
            ),
        ),
        security_types=("fund", "etf"),
    ),
    _PhaseRule(
        exchange="sh",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=(
            _PhaseInterval(
                time(9, 25),
                time(9, 25, 1),
                False,
                MarketPhase.AUCTION,
            ),
            *_TRADE_REGULAR_AM,
            *_TRADE_BOND_SH_PM,
        ),
        security_types=("bond", "convertible_bond", "bond_repo"),
    ),
)


def resolve_level2_phase(
    *,
    table: pa.Table,
    exchange: Literal["sh", "sz"],
    trade_date: str,
) -> pa.Table:
    """Append the execution-mechanism phase to normalized Level-2 trades.

    Example:
        resolved = resolve_level2_phase(
            table=trade_table,
            exchange="sh",
            trade_date="2026-07-27",
        )
    """
    trade_day = date.fromisoformat(trade_date)
    matches = [
        rule
        for rule in _DEFAULT_A_SHARE_TRADE_PHASE_RULES
        if rule.exchange == exchange
        and trade_day >= rule.effective_from
        and (rule.effective_to is None or trade_day <= rule.effective_to)
    ]
    if not matches:
        raise ValueError(
            f"no trade phase rule matched: exchange={exchange}, trade_date={trade_date}"
        )

    ts_utc = pc.cast(table["ts_utc"], pa.int64())
    trade_day_start = DateTimeUtils.local_time_to_utc_epoch_us(
        time(0, 0),
        trade_day,
        timezone=DateTimeUtils.MARKET_TIMEZONE,
    )
    next_day_start = DateTimeUtils.local_time_to_utc_epoch_us(
        time(0, 0),
        trade_day + timedelta(days=1),
        timezone=DateTimeUtils.MARKET_TIMEZONE,
    )
    local_date_mask = pc.and_(
        pc.greater_equal(ts_utc, pa.scalar(trade_day_start, type=pa.int64())),
        pc.less(ts_utc, pa.scalar(next_day_start, type=pa.int64())),
    )
    if pc.any(pc.invert(local_date_mask)).as_py():
        raise ValueError(f"ts_utc local date does not match trade_date={trade_date}")

    phase = pa.nulls(table.num_rows, type=pa.int8())
    supported = pa.repeat(pa.scalar(False), table.num_rows)
    security_type = table["security_type"].combine_chunks()

    for rule in matches:
        scope_mask = pc.is_in(
            security_type,
            value_set=pa.array(rule.security_types, type=pa.string()),
        )
        supported = pc.or_(supported, scope_mask)

        for interval in rule.intervals:
            start_ts = DateTimeUtils.local_time_to_utc_epoch_us(
                interval.start_local_time,
                trade_day,
                timezone=DateTimeUtils.MARKET_TIMEZONE,
            )
            end_ts = DateTimeUtils.local_time_to_utc_epoch_us(
                interval.end_local_time,
                trade_day,
                timezone=DateTimeUtils.MARKET_TIMEZONE,
            )

            start_mask = pc.greater_equal(
                ts_utc,
                pa.scalar(start_ts, type=pa.int64()),
            )
            if interval.end_inclusive:
                end_mask = pc.less_equal(
                    ts_utc,
                    pa.scalar(end_ts, type=pa.int64()),
                )
            else:
                end_mask = pc.less(ts_utc, pa.scalar(end_ts, type=pa.int64()))

            mask = pc.and_(scope_mask, pc.and_(start_mask, end_mask))
            phase = pc.if_else(
                mask,
                pa.scalar(int(interval.phase), type=pa.int8()),
                phase,
            )

    unsupported = pc.invert(supported)
    if pc.any(unsupported).as_py():
        unsupported_types = pc.unique(pc.filter(security_type, unsupported)).to_pylist()
        raise ValueError(
            "no trade phase rule matched security_type: "
            f"exchange={exchange}, trade_date={trade_date}, "
            f"security_type={unsupported_types}"
        )

    unmatched = pc.is_null(phase)
    if pc.any(unmatched).as_py():
        unmatched_rows = pc.sum(pc.cast(unmatched, pa.int64())).as_py()
        raise ValueError(
            "trade rows fall outside defined phase intervals: "
            f"exchange={exchange}, trade_date={trade_date}, "
            f"rows={unmatched_rows}"
        )

    return table.append_column("phase", phase)
