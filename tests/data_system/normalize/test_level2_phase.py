# filepath: tests/data_system/normalize/test_level2_phase.py
"""Behavior tests for effective-dated Level-2 trade phases."""

from __future__ import annotations

from collections.abc import Sequence
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


@pytest.mark.parametrize("security_type", ["stock", "cdr"])
@pytest.mark.parametrize("trade_date", [date(2018, 8, 20), date(2026, 9, 17)])
def test_resolve_classifies_sh_1457_resume_call_as_auction(
    security_type: str,
    trade_date: date,
) -> None:
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(moment, trade_date)
                    for moment in (
                        time(14, 57),
                        time(14, 57, 0, 30_000),
                        time(14, 57, 0, 990_000),
                    )
                ],
                type=pa.int64(),
            ),
            "security_type": [security_type] * 3,
        }
    )

    resolved = resolve_level2_phase(
        table=table,
        exchange="sh",
        trade_date=trade_date.isoformat(),
    )

    assert resolved["phase"].to_pylist() == [int(MarketPhase.AUCTION)] * 3


@pytest.mark.parametrize("security_type", ["stock", "cdr"])
def test_resolve_rejects_sh_1457_resume_at_one_second(
    security_type: str,
) -> None:
    trade_date = date(2026, 9, 17)
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
            "security_type": [security_type],
        }
    )

    with pytest.raises(ValueError, match="outside defined phase intervals"):
        resolve_level2_phase(
            table=table,
            exchange="sh",
            trade_date=trade_date.isoformat(),
        )


@pytest.mark.parametrize("security_type", ["b_share", "fund", "etf"])
def test_resolve_does_not_extend_sh_1457_resume_to_other_security_types(
    security_type: str,
) -> None:
    trade_date = date(2026, 9, 17)
    table = pa.table(
        {
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(
                        time(14, 57, 0, 30_000),
                        trade_date,
                    )
                ],
                type=pa.int64(),
            ),
            "security_type": [security_type],
        }
    )

    with pytest.raises(ValueError, match="outside defined phase intervals"):
        resolve_level2_phase(
            table=table,
            exchange="sh",
            trade_date=trade_date.isoformat(),
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


@pytest.mark.parametrize(
    ("symbol", "security_type", "moment", "expected"),
    [
        ("143728", "bond", time(9, 14, 7, 100_000), MarketPhase.CONTINUOUS),
        ("102339", "bond", time(15, 29, 57, 420_000), MarketPhase.CONTINUOUS),
        ("102339", "bond", time(15), MarketPhase.CONTINUOUS),
        ("131800", "bond_repo", time(9, 25), MarketPhase.AUCTION),
        ("131810", "bond_repo", time(15, 29, 59, 980_000), MarketPhase.CONTINUOUS),
        ("131810", "bond_repo", time(15), MarketPhase.CONTINUOUS),
        ("121615", "fund", time(15, 3, 14, 750_000), MarketPhase.CONTINUOUS),
        ("121615", "fund", time(15, 3, 18, 460_000), MarketPhase.CONTINUOUS),
        (
            "117256", "convertible_bond", time(15, 6, 5, 570_000),
            MarketPhase.CONTINUOUS,
        ),
        (
            "117251", "convertible_bond", time(15, 19, 13, 980_000),
            MarketPhase.CONTINUOUS,
        ),
        ("121615", "fund", time(9, 25), MarketPhase.CONTINUOUS),
        ("117256", "convertible_bond", time(15), MarketPhase.CONTINUOUS),
        ("117000", "convertible_bond", time(15, 6), MarketPhase.CONTINUOUS),
        ("117499", "convertible_bond", time(15, 6), MarketPhase.CONTINUOUS),
        ("121500", "fund", time(15, 6), MarketPhase.CONTINUOUS),
        ("121999", "fund", time(15, 6), MarketPhase.CONTINUOUS),
    ],
)
def test_resolve_classifies_sz_baidu_fixed_income_trades(
    symbol: str,
    security_type: str,
    moment: time,
    expected: MarketPhase,
) -> None:
    trade_day = date(2026, 9, 21)
    table = pa.table(
        {
            "symbol": [symbol],
            "ts_utc": pa.array(
                [DateTimeUtils.local_time_to_utc_epoch_us(moment, trade_day)],
                type=pa.int64(),
            ),
            "security_type": [security_type],
        }
    )

    resolved = resolve_level2_phase(
        table=table,
        exchange="sz",
        trade_date=trade_day.isoformat(),
    )

    assert resolved["phase"].to_pylist() == [int(expected)]
    assert resolved.drop_columns("phase").equals(table)


@pytest.mark.parametrize(
    ("symbol", "security_type", "moments"),
    [
        (
            "143728", "bond",
            [time(9), time(11, 30), time(13), time(15, 29, 59, 999_000)],
        ),
        (
            "121615", "fund",
            [time(9), time(11, 30), time(13), time(15, 29, 59, 999_000)],
        ),
        (
            "117256", "convertible_bond",
            [time(9), time(11, 30), time(13), time(15, 29, 59, 999_000)],
        ),
        (
            "131810", "bond_repo",
            [time(9, 30), time(11, 30), time(13), time(15, 29, 59, 999_000)],
        ),
    ],
)
def test_resolve_accepts_sz_fixed_income_continuous_boundaries(
    symbol: str,
    security_type: str,
    moments: Sequence[time],
) -> None:
    trade_day = date(2026, 9, 21)
    table = pa.table(
        {
            "symbol": [symbol] * len(moments),
            "ts_utc": pa.array(
                [
                    DateTimeUtils.local_time_to_utc_epoch_us(moment, trade_day)
                    for moment in moments
                ],
                type=pa.int64(),
            ),
            "security_type": [security_type] * len(moments),
        }
    )

    resolved = resolve_level2_phase(
        table=table,
        exchange="sz",
        trade_date=trade_day.isoformat(),
    )

    assert resolved["phase"].to_pylist() == [
        int(MarketPhase.CONTINUOUS)
    ] * len(moments)


@pytest.mark.parametrize(
    ("symbol", "security_type"),
    [
        ("143728", "bond"),
        ("121615", "fund"),
        ("117256", "convertible_bond"),
        ("131810", "bond_repo"),
    ],
)
@pytest.mark.parametrize(
    "moment",
    [
        time(8, 59, 59, 999_000),
        time(11, 30, 0, 1_000),
        time(12, 59, 59, 999_000),
        time(15, 30),
    ],
)
def test_resolve_rejects_sz_fixed_income_outside_sessions(
    symbol: str,
    security_type: str,
    moment: time,
) -> None:
    trade_day = date(2026, 9, 21)
    table = pa.table(
        {
            "symbol": [symbol],
            "ts_utc": pa.array(
                [DateTimeUtils.local_time_to_utc_epoch_us(moment, trade_day)],
                type=pa.int64(),
            ),
            "security_type": [security_type],
        }
    )

    with pytest.raises(ValueError, match="outside defined phase intervals"):
        resolve_level2_phase(
            table=table,
            exchange="sz",
            trade_date=trade_day.isoformat(),
        )


@pytest.mark.parametrize(
    ("symbol", "security_type"),
    [
        ("000001", "stock"),
        ("180101", "fund"),
        ("159001", "etf"),
        ("123001", "convertible_bond"),
        ("115999", "convertible_bond"),
        ("121499", "convertible_bond"),
    ],
)
def test_resolve_does_not_extend_sz_fixed_income_sessions_to_other_symbols(
    symbol: str,
    security_type: str,
) -> None:
    trade_day = date(2026, 9, 21)
    table = pa.table(
        {
            "symbol": [symbol],
            "ts_utc": pa.array(
                [DateTimeUtils.local_time_to_utc_epoch_us(time(15, 6), trade_day)],
                type=pa.int64(),
            ),
            "security_type": [security_type],
        }
    )

    with pytest.raises(ValueError, match="outside defined phase intervals"):
        resolve_level2_phase(
            table=table,
            exchange="sz",
            trade_date=trade_day.isoformat(),
        )


def test_resolve_preserves_sz_phase_before_baidu_coverage_date() -> None:
    trade_day = date(2026, 9, 20)
    table = pa.table(
        {
            "symbol": ["143728"],
            "ts_utc": pa.array(
                [DateTimeUtils.local_time_to_utc_epoch_us(time(15, 6), trade_day)],
                type=pa.int64(),
            ),
            "security_type": ["bond"],
        }
    )

    with pytest.raises(ValueError, match="outside defined phase intervals"):
        resolve_level2_phase(
            table=table,
            exchange="sz",
            trade_date=trade_day.isoformat(),
        )
