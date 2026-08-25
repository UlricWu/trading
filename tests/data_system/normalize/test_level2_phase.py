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
    (date(2025, 12, 25), "stock"),
    (date(2025, 12, 25), "cdr"),
    (date(2025, 12, 25), "b_share"),
    (date(2025, 12, 25), "fund"),
    (date(2025, 12, 25), "etf"),
    (date(2025, 12, 25), "bond"),
    (date(2025, 12, 25), "convertible_bond"),
    (date(2025, 12, 25), "bond_repo"),
    (date(2026, 7, 14), "fund"),
    (date(2026, 7, 14), "etf"),
)


def test_resolve_classifies_observed_sh_delayed_opening_publish_as_auction() -> None:
    trade_date = date(2026, 3, 2)
    delayed_publish = DateTimeUtils.local_time_to_utc_epoch_us(
        time(9, 25, 3, 190_000),
        trade_date,
    )
    table = pa.table(
        {
            "ts_utc": pa.array([delayed_publish] * 3, type=pa.int64()),
            "security_type": pa.array(["stock", "etf", "fund"]),
        }
    )

    resolved = resolve_level2_phase(
        table=table,
        exchange="sh",
        trade_date=trade_date.isoformat(),
    )

    assert resolved["phase"].to_pylist() == [int(MarketPhase.AUCTION)] * 3


def test_resolve_classifies_observed_sh_20260727_opening_publish_as_auction() -> None:
    trade_date = date(2026, 7, 27)
    delayed_publish = DateTimeUtils.local_time_to_utc_epoch_us(
        time(9, 25, 12, 890_000),
        trade_date,
    )
    table = pa.table(
        {
            "ts_utc": pa.array([delayed_publish] * 3, type=pa.int64()),
            "security_type": pa.array(["stock", "etf", "fund"]),
        }
    )

    resolved = resolve_level2_phase(
        table=table,
        exchange="sh",
        trade_date=trade_date.isoformat(),
    )

    assert resolved["phase"].to_pylist() == [int(MarketPhase.AUCTION)] * 3


@pytest.mark.parametrize(
    ("trade_date", "security_type"),
    _SH_OPEN_CALL_BOUNDARY_CASES,
)
def test_resolve_classifies_last_sh_opening_publish_centisecond_as_auction(
    trade_date: date,
    security_type: str,
) -> None:
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(9, 25, 12, 990_000),
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
    ("trade_date", "security_type"),
    _SH_OPEN_CALL_BOUNDARY_CASES,
)
def test_resolve_rejects_sh_opening_publish_at_thirteen_seconds(
    trade_date: date,
    security_type: str,
) -> None:
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(9, 25, 13),
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


def test_resolve_classifies_observed_sz_stock_1457_resume_as_auction() -> None:
    trade_date = date(2026, 4, 30)
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(14, 57),
                        trade_date,
                    ),
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(14, 57, 0, 999_000),
                        trade_date,
                    ),
                ],
                type=pa.int64(),
            ),
            "security_type": pa.array(["stock", "stock"]),
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


def test_resolve_rejects_sz_stock_1457_resume_at_one_second() -> None:
    trade_date = date(2026, 4, 30)
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
            "security_type": pa.array(["stock"]),
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


def test_resolve_rejects_sz_fund_at_1457_resume_time() -> None:
    trade_date = date(2026, 4, 30)
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
            "security_type": pa.array(["fund"]),
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
