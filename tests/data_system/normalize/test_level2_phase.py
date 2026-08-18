# filepath: tests/data_system/normalize/test_level2_phase.py
"""Behavior tests for effective-dated Level-2 trade phases."""

from __future__ import annotations

from datetime import date, time

import pyarrow as pa
import pytest

from src.data_system.market_phase import MarketPhase
from src.data_system.normalize.level2_phase import resolve_level2_phase
from src.utils.datetime_utils import DateTimeUtils


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
