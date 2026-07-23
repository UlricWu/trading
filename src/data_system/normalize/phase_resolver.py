# filepath: src/data_system/normalize/phase_resolver.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.compute as pc

from src import logs
from src.data_system.arrow.ops import append_or_replace
from src.utils.datetime_utils import DateTimeUtils
from src.pipeline.phase import MarketPhase


PhaseEventKind = Literal["order", "trade"]


@dataclass(frozen=True)
class PhaseInterval:
    """One local-time interval in a phase rule."""

    start_local_time: time
    end_local_time: time
    end_inclusive: bool
    phase: MarketPhase

    def contains(self, value: time) -> bool:
        """Return whether `value` falls in this interval."""
        if value < self.start_local_time:
            return False
        if self.end_inclusive:
            return value <= self.end_local_time
        return value < self.end_local_time


@dataclass(frozen=True)
class PhaseRule:
    """Effective-dated phase rule for one market scope."""

    exchange: str
    kind: PhaseEventKind
    effective_from: date
    effective_to: date | None
    intervals: tuple[PhaseInterval, ...]
    security_types: tuple[str, ...] | None = None
    timezone: str = "Asia/Shanghai"
    priority: int = 0

    def matches(
        self,
        *,
        exchange: str,
        kind: PhaseEventKind,
        trade_date: str,
    ) -> bool:
        """Return whether this rule applies to the requested event scope."""
        if self.exchange != exchange:
            return False
        if self.kind != kind:
            return False
        trade_day = date.fromisoformat(trade_date)

        if trade_day < self.effective_from:
            return False
        if self.effective_to is not None and trade_day > self.effective_to:
            return False
        return True


_EARLIEST_SUPPORTED_DATE = date(1900, 1, 1)
_SSE_CLOSE_CALL_EFFECTIVE_DATE = date(2018, 8, 20)


def _phase_rule(
    *,
    exchange: str,
    kind: PhaseEventKind,
    effective_from: date,
    effective_to: date | None,
    intervals: tuple[PhaseInterval, ...],
    security_types: tuple[str, ...] | None = None,
) -> PhaseRule:
    return PhaseRule(
        exchange=exchange,
        kind=kind,
        effective_from=effective_from,
        effective_to=effective_to,
        intervals=intervals,
        security_types=security_types,
    )


_ORDER_OPEN_CALL_AND_REGULAR_AM = (
    PhaseInterval(time(9, 15), time(9, 25), True, MarketPhase.AUCTION),
    PhaseInterval(time(9, 30), time(11, 30), True, MarketPhase.TRADING),
)

_ORDER_REGULAR_PM_WITH_CLOSE_CALL = (
    PhaseInterval(time(13, 0), time(14, 57), False, MarketPhase.TRADING),
    PhaseInterval(time(14, 57), time(15, 0), True, MarketPhase.AUCTION),
)

_TRADE_STOCK_PM = (
    PhaseInterval(time(13, 0), time(14, 57), False, MarketPhase.TRADING),
)

_TRADE_FUND_ETF_SH_PM = (
    PhaseInterval(time(13, 0), time(15, 0), False, MarketPhase.TRADING),
)

_TRADE_BOND_SH_PM = (
    PhaseInterval(time(13, 0), time(15, 30), False, MarketPhase.TRADING),
)

_TRADE_REGULAR_AM = (
    PhaseInterval(time(9, 30), time(11, 30), True, MarketPhase.TRADING),
)

_SZ_OPEN_AND_CLOSE_CALL = (
    PhaseInterval(time(9, 25), time(9, 25, 1), False, MarketPhase.AUCTION),
    PhaseInterval(time(15, 0), time(15, 0, 1), False, MarketPhase.AUCTION),
)

DEFAULT_A_SHARE_PHASE_RULES: tuple[PhaseRule, ...] = (
    _phase_rule(
        exchange="sh",
        kind="order",
        effective_from=_SSE_CLOSE_CALL_EFFECTIVE_DATE,
        effective_to=None,
        intervals=_ORDER_OPEN_CALL_AND_REGULAR_AM + _ORDER_REGULAR_PM_WITH_CLOSE_CALL,
        security_types=("stock",),
    ),
    _phase_rule(
        exchange="sz",
        kind="order",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=_ORDER_OPEN_CALL_AND_REGULAR_AM + _ORDER_REGULAR_PM_WITH_CLOSE_CALL,
        security_types=("stock",),
    ),
    _phase_rule(
        exchange="sh",
        kind="trade",
        effective_from=_SSE_CLOSE_CALL_EFFECTIVE_DATE,
        effective_to=None,
        intervals=(
            PhaseInterval(time(9, 25), time(9, 25, 2), False, MarketPhase.AUCTION),
            *_TRADE_REGULAR_AM,
            *_TRADE_STOCK_PM,
            PhaseInterval(time(15, 0), time(15, 0, 3), False, MarketPhase.AUCTION),
        ),
        security_types=("stock", "cdr"),
    ),
    _phase_rule(
        exchange="sh",
        kind="trade",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=(
            PhaseInterval(time(9, 25), time(9, 25, 1), False, MarketPhase.AUCTION),
            *_TRADE_REGULAR_AM,
            *_TRADE_STOCK_PM,
            PhaseInterval(time(15, 0), time(15, 0, 1), False, MarketPhase.AUCTION),
        ),
        security_types=("b_share",),
    ),
    _phase_rule(
        exchange="sz",
        kind="trade",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=_SZ_OPEN_AND_CLOSE_CALL + _TRADE_REGULAR_AM + _TRADE_STOCK_PM,
        security_types=("stock", "fund", "etf", "bond", "convertible_bond"),
    ),
    _phase_rule(
        exchange="sz",
        kind="trade",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=_SZ_OPEN_AND_CLOSE_CALL + _TRADE_REGULAR_AM + _TRADE_STOCK_PM,
        security_types=("b_share",),
    ),
    _phase_rule(
        exchange="sh",
        kind="trade",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=(
            PhaseInterval(time(9, 25), time(9, 25, 2), False, MarketPhase.AUCTION),
            *_TRADE_REGULAR_AM,
            *_TRADE_FUND_ETF_SH_PM,
        ),
        security_types=("fund", "etf"),
    ),
    _phase_rule(
        exchange="sh",
        kind="trade",
        effective_from=_EARLIEST_SUPPORTED_DATE,
        effective_to=None,
        intervals=(
            PhaseInterval(time(9, 25), time(9, 25, 1), False, MarketPhase.AUCTION),
            *_TRADE_REGULAR_AM,
            *_TRADE_BOND_SH_PM,
        ),
        security_types=("bond", "convertible_bond", "bond_repo"),
    ),
)


class PhaseResolver:
    """Pure event timestamp phase classifier."""

    def __init__(
        self, rules: tuple[PhaseRule, ...] = DEFAULT_A_SHARE_PHASE_RULES
    ) -> None:
        self._rules = rules

    @staticmethod
    def phase_code(phase: str) -> int:
        """Return the stable integer code for one market phase name."""
        try:
            return int(MarketPhase[phase.upper()])
        except KeyError as exc:
            raise ValueError(f"unsupported market phase: {phase!r}") from exc

    def resolve(
        self,
        *,
        table: pa.Table,
        exchange: str,
        trade_date: str,
        kind: PhaseEventKind = "order",
        ts_utc_col: str = "ts_utc",
        security_type_col: str = "security_type",
        col: str = "phase",
    ) -> pa.Table:
        """Resolve one UTC event timestamp to a market phase."""
        matches = [
            rule
            for rule in self._rules
            if rule.matches(exchange=exchange, kind=kind, trade_date=trade_date)
        ]
        if not matches:
            raise ValueError(
                f"no phase rule matched: exchange={exchange}, kind={kind}, trade_date={trade_date}"
            )

        logs.info(
            f"[PhaseResolver] resolved_rules exchange={exchange} "
            f"kind={kind} rule_count={len(matches)}"
        )
        trade_day = date.fromisoformat(trade_date)
        ts_utc = pc.cast(table[ts_utc_col], pa.int64())
        exchange_tz = ZoneInfo(matches[0].timezone)
        trade_day_start = DateTimeUtils.local_time_to_utc_epoch_us(
            time(0, 0),
            trade_day,
            timezone=exchange_tz,
        )
        next_day_start = DateTimeUtils.local_time_to_utc_epoch_us(
            time(0, 0),
            trade_day + timedelta(days=1),
            timezone=exchange_tz,
        )
        local_date_mask = pc.and_(
            pc.greater_equal(ts_utc, pa.scalar(trade_day_start, type=pa.int64())),
            pc.less(ts_utc, pa.scalar(next_day_start, type=pa.int64())),
        )
        if pc.any(pc.invert(local_date_mask)).as_py():
            raise ValueError(
                f"ts_utc local date does not match trade_date={trade_date}"
            )

        phase = pa.repeat(
            pa.scalar(int(MarketPhase.BREAK), type=pa.int8()),
            table.num_rows,
        )
        supported = pa.repeat(pa.scalar(False), table.num_rows)

        security_type = None
        if security_type_col in table.column_names:
            security_type = table[security_type_col].combine_chunks()

        for rule in matches:
            exchange_tz = ZoneInfo(rule.timezone)
            if rule.security_types is None or security_type is None:
                scope_mask = pa.repeat(pa.scalar(True), table.num_rows)
            else:
                scope_mask = pc.is_in(
                    security_type,
                    value_set=pa.array(rule.security_types, type=pa.string()),
                )
            supported = pc.or_(supported, scope_mask)

            for r in rule.intervals:
                start_ts = DateTimeUtils.local_time_to_utc_epoch_us(
                    r.start_local_time,
                    trade_day,
                    timezone=exchange_tz,
                )
                end_ts = DateTimeUtils.local_time_to_utc_epoch_us(
                    r.end_local_time,
                    trade_day,
                    timezone=exchange_tz,
                )

                start_mask = pc.greater_equal(
                    ts_utc,
                    pa.scalar(start_ts, type=pa.int64()),
                )

                if r.end_inclusive:
                    end_mask = pc.less_equal(ts_utc, pa.scalar(end_ts, type=pa.int64()))
                else:
                    end_mask = pc.less(ts_utc, pa.scalar(end_ts, type=pa.int64()))

                mask = pc.and_(scope_mask, pc.and_(start_mask, end_mask))

                phase = pc.if_else(
                    mask,
                    pa.scalar(int(r.phase), type=pa.int8()),
                    phase,
                )

        unsupported = pc.invert(supported)
        if pc.any(unsupported).as_py():
            if security_type is None:
                raise ValueError(
                    f"no phase rule matched rows: exchange={exchange}, kind={kind}, trade_date={trade_date}"
                )
            unsupported_types = pc.unique(
                pc.filter(security_type, unsupported)
            ).to_pylist()
            raise ValueError(
                "no phase rule matched security_type: "
                f"exchange={exchange}, kind={kind}, trade_date={trade_date}, "
                f"security_type={unsupported_types}"
            )

        return append_or_replace(table, col, phase)
