# filepath: tests/data_system/normalize/test_level2_phase.py
"""Behavior tests for effective-dated Level-2 trade phases."""

from __future__ import annotations

from datetime import date, time

import pyarrow as pa
import pytest

from src.data_system.market_phase import MarketPhase
from src.data_system.normalize.level2_phase import resolve_level2_phase
from src.utils.datetime_utils import DateTimeUtils


_SH_OPEN_CALL_BOUNDARY_CASES = (
    (date(2025, 11, 18), "stock", 2),
    (date(2025, 11, 18), "cdr", 2),
    (date(2025, 11, 18), "fund", 2),
    (date(2025, 11, 18), "etf", 2),
    (date(2025, 12, 3), "b_share", 1),
    (date(2026, 7, 14), "fund", 2),
    (date(2026, 7, 14), "etf", 2),
)


@pytest.mark.parametrize(
    ("trade_date", "security_type", "boundary_second"),
    _SH_OPEN_CALL_BOUNDARY_CASES,
)
def test_resolve_classifies_exact_sh_open_call_boundary_as_auction(
    trade_date: date,
    security_type: str,
    boundary_second: int,
) -> None:
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(9, 25, boundary_second),
                        trade_date,
                    )
                ],
                type=pa.int64(),
            ),
            "security_type": pa.array([security_type]),
        }
    )

    resolved = resolve_level2_phase(
        table=table,
        exchange="sh",
        trade_date=trade_date.isoformat(),
    )

    assert resolved["phase"].to_pylist() == [int(MarketPhase.AUCTION)]


@pytest.mark.parametrize(
    ("trade_date", "security_type", "boundary_second"),
    _SH_OPEN_CALL_BOUNDARY_CASES,
)
def test_resolve_rejects_next_sh_open_call_centisecond(
    trade_date: date,
    security_type: str,
    boundary_second: int,
) -> None:
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(9, 25, boundary_second, 10_000),
                        trade_date,
                    )
                ],
                type=pa.int64(),
            ),
            "security_type": pa.array([security_type]),
        }
    )

    with pytest.raises(
        ValueError,
        match=r"trade rows fall outside defined phase intervals: .*rows=1",
    ):
        resolve_level2_phase(
            table=table,
            exchange="sh",
            trade_date=trade_date.isoformat(),
        )


def test_resolve_classifies_late_sh_b_share_close_prints_as_auction() -> None:
    trade_date = date(2026, 7, 14)
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(15, 0, 1, 10_000),
                        trade_date,
                    ),
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(15, 0, 1, 999_999),
                        trade_date,
                    ),
                ],
                type=pa.int64(),
            ),
            "security_type": pa.array(["b_share", "b_share"]),
        }
    )

    resolved = resolve_level2_phase(
        table=table,
        exchange="sh",
        trade_date=trade_date.isoformat(),
    )

    assert resolved["phase"].to_pylist() == [
        int(MarketPhase.AUCTION),
        int(MarketPhase.AUCTION),
    ]


def test_resolve_rejects_sh_b_share_close_print_at_two_seconds() -> None:
    trade_date = date(2026, 7, 14)
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(15, 0, 2),
                        trade_date,
                    )
                ],
                type=pa.int64(),
            ),
            "security_type": pa.array(["b_share"]),
        }
    )

    with pytest.raises(
        ValueError,
        match=r"trade rows fall outside defined phase intervals: .*rows=1",
    ):
        resolve_level2_phase(
            table=table,
            exchange="sh",
            trade_date=trade_date.isoformat(),
        )


def test_resolve_classifies_sz_convertible_bond_1457_resume_prints_as_auction() -> None:
    trade_date = date(2026, 7, 14)
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(14, 57),
                        trade_date,
                    ),
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(14, 57, 0, 999_999),
                        trade_date,
                    ),
                ],
                type=pa.int64(),
            ),
            "security_type": pa.array(["convertible_bond", "convertible_bond"]),
        }
    )

    resolved = resolve_level2_phase(
        table=table,
        exchange="sz",
        trade_date=trade_date.isoformat(),
    )

    assert resolved["phase"].to_pylist() == [
        int(MarketPhase.AUCTION),
        int(MarketPhase.AUCTION),
    ]


def test_resolve_rejects_sz_convertible_bond_1457_print_at_one_second() -> None:
    trade_date = date(2026, 7, 14)
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(14, 57, 1),
                        trade_date,
                    )
                ],
                type=pa.int64(),
            ),
            "security_type": pa.array(["convertible_bond"]),
        }
    )

    with pytest.raises(
        ValueError,
        match=r"trade rows fall outside defined phase intervals: .*rows=1",
    ):
        resolve_level2_phase(
            table=table,
            exchange="sz",
            trade_date=trade_date.isoformat(),
        )


def test_resolve_rejects_sz_convertible_bond_1457_print_before_resume_rule() -> None:
    trade_date = date(2020, 6, 7)
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(14, 57),
                        trade_date,
                    )
                ],
                type=pa.int64(),
            ),
            "security_type": pa.array(["convertible_bond"]),
        }
    )

    with pytest.raises(
        ValueError,
        match=r"trade rows fall outside defined phase intervals: .*rows=1",
    ):
        resolve_level2_phase(
            table=table,
            exchange="sz",
            trade_date=trade_date.isoformat(),
        )
