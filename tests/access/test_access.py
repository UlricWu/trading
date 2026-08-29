# filepath: tests/access/test_access.py
"""Behavior tests for the public Access boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.access import access as access_module
from src.utils.path import PathManager


def test_access_requires_safe_processed_version(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)

    with pytest.raises(ValueError, match="processed_version"):
        Access(pm=pm, processed_version="../v1")


def test_daily_bars_reads_full_object_and_requested_symbol_order(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame(
            {
                "symbol": ["000001", "600000", "000002"],
                "trade_date": [trade_date, trade_date, trade_date],
                "close": [12.0, 20.0, 8.0],
            }
        ),
    )
    access = Access(pm=pm, processed_version="v1")

    complete = access.daily_bars(trade_date=trade_date)
    selected = access.daily_bars(
        trade_date=trade_date,
        symbols=["600000", "000001"],
    )
    empty = access.daily_bars(trade_date=trade_date, symbols=())

    assert complete["symbol"].tolist() == ["000001", "600000", "000002"]
    assert selected["symbol"].tolist() == ["600000", "000001"]
    assert selected["close"].tolist() == [20.0, 12.0]
    assert empty.empty
    assert empty.columns.tolist() == ["symbol", "trade_date", "close"]


def test_daily_bars_rejects_missing_object_and_invalid_symbol_request(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    access = Access(pm=pm, processed_version="v1")
    trade_date = "2026-05-06"

    with pytest.raises(FileNotFoundError, match="required Meta"):
        access.daily_bars(trade_date=trade_date)

    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["000001"], "trade_date": [trade_date]}),
    )
    with pytest.raises(TypeError, match="sequence"):
        access.daily_bars(trade_date=trade_date, symbols="000001")
    with pytest.raises(ValueError, match="six-digit"):
        access.daily_bars(trade_date=trade_date, symbols=["1"])
    with pytest.raises(ValueError, match="unique"):
        access.daily_bars(
            trade_date=trade_date,
            symbols=["000001", "000001"],
        )
    with pytest.raises(KeyError, match="600000"):
        access.daily_bars(trade_date=trade_date, symbols=["600000"])


def test_named_daily_objects_validate_identity_and_project_owned_columns(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "adj_factor",
        pd.DataFrame(
            {
                "symbol": ["000001", "600000"],
                "trade_date": [trade_date, trade_date],
                "adj_factor": [1.2, 3.4],
                "unused": [1, 2],
            }
        ),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "daily_basic",
        pd.DataFrame(
            {
                "symbol": ["000001", "600000"],
                "trade_date": [trade_date, trade_date],
                "turnover_rate": [0.0, 2.5],
                "unused": [1, 2],
            }
        ),
    )
    access = Access(pm=pm, processed_version="v1")

    factors = access.adjustment_factors(
        trade_date=trade_date,
        symbols=("600000", "000001"),
    )
    turnover = access.turnover_rates(trade_date=trade_date)

    assert factors.to_dict("list") == {
        "symbol": ["600000", "000001"],
        "trade_date": [trade_date, trade_date],
        "adj_factor": [3.4, 1.2],
    }
    assert turnover.to_dict("list") == {
        "symbol": ["000001", "600000"],
        "trade_date": [trade_date, trade_date],
        "turnover_rate": [0.0, 2.5],
    }


def test_named_daily_objects_reject_partition_identity_mismatch(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "adj_factor",
        pd.DataFrame(
            {
                "symbol": ["000001"],
                "trade_date": ["2026-05-05"],
                "adj_factor": [1.0],
            }
        ),
    )

    with pytest.raises(ValueError, match="must equal requested date"):
        Access(pm=pm, processed_version="v1").adjustment_factors(trade_date=trade_date)


def test_trade_dates_read_annual_calendar_objects_in_ascending_order(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    _write_calendar_year(
        pm,
        2025,
        pd.DataFrame(
            {
                "trade_date": ["2025-12-30", "2025-12-31"],
                "is_open": [False, True],
            }
        ),
    )
    _write_calendar_year(
        pm,
        2026,
        pd.DataFrame(
            {
                "trade_date": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "is_open": [False, True, True],
            }
        ),
    )

    access = Access(pm=pm, processed_version="v1")

    assert access.trade_dates(
        start_date="2025-12-30",
        end_date="2026-01-03",
    ) == [
        "2025-12-31",
        "2026-01-02",
        "2026-01-03",
    ]
    assert access.recent_trade_dates(
        end_date="2026-01-03",
        sessions=3,
    ) == [
        "2025-12-31",
        "2026-01-02",
        "2026-01-03",
    ]


def test_trade_dates_requires_every_requested_calendar_year(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _write_calendar_year(
        pm,
        2025,
        pd.DataFrame(
            {
                "trade_date": ["2025-12-31"],
                "is_open": [True],
            }
        ),
    )

    with pytest.raises(FileNotFoundError, match="required Meta"):
        Access(pm=pm, processed_version="v1").trade_dates(
            start_date="2025-12-31",
            end_date="2026-01-01",
        )


def test_universe_applies_listing_st_and_suspension_filters(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame(
            {
                "symbol": [
                    "600000",
                    "000004",
                    "000003",
                    "000002",
                    "000001",
                ]
            }
        ),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "stock_basic",
        pd.DataFrame(
            {
                "symbol": [
                    "000001",
                    "000002",
                    "000003",
                    "000004",
                    "600000",
                ],
                "list_date": [
                    "2000-01-01",
                    "2000-01-01",
                    "2000-01-01",
                    "2026-04-20",
                    "2000-01-01",
                ],
            }
        ),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "stock_st",
        pd.DataFrame({"symbol": ["000002"]}),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "suspend_d",
        pd.DataFrame({"symbol": ["000003"]}),
    )

    symbols = Access(pm=pm, processed_version="v1").universe(
        trade_date=trade_date,
        min_listing_calendar_days=30,
    )

    assert symbols == ("000001", "600000")


def test_universe_zero_listing_days_uses_historical_stock_list(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["600000", "000001", "000002"]}),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "stock_basic",
        pd.DataFrame({"symbol": ["600000", "000002", "300001"]}),
    )
    _write_empty_universe_exclusions(pm=pm, trade_date=trade_date)

    assert Access(pm=pm, processed_version="v1").universe(
        trade_date=trade_date,
        min_listing_calendar_days=0,
    ) == ("000002", "600000")


def test_universe_consumes_an_empty_historical_stock_list(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2019-04-01"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["600000", "000001"]}),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "stock_basic",
        pd.DataFrame(
            {
                "symbol": pd.Series(dtype="string"),
                "list_date": pd.Series(dtype="string"),
            }
        ),
    )
    _write_empty_universe_exclusions(pm=pm, trade_date=trade_date)

    assert Access(pm=pm, processed_version="v1").universe(
        trade_date=trade_date,
        min_listing_calendar_days=30,
    ) == ()


def test_universe_zero_listing_days_requires_historical_stock_list(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["000001"]}),
    )
    _write_empty_universe_exclusions(pm=pm, trade_date=trade_date)

    with pytest.raises(FileNotFoundError, match="required Meta"):
        Access(pm=pm, processed_version="v1").universe(
            trade_date=trade_date,
            min_listing_calendar_days=0,
        )


def test_universe_includes_exact_listing_age_boundary(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["000001", "000002"]}),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "stock_basic",
        pd.DataFrame(
            {
                "symbol": ["000001", "000002"],
                "list_date": ["2026-04-06", "2026-04-07"],
            }
        ),
    )
    _write_empty_universe_exclusions(pm=pm, trade_date=trade_date)

    assert Access(pm=pm, processed_version="v1").universe(
        trade_date=trade_date,
        min_listing_calendar_days=30,
    ) == ("000001",)


def test_universe_excludes_missing_and_null_listing_dates(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["000001", "000002", "000003"]}),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "stock_basic",
        pd.DataFrame(
            {
                "symbol": ["000001", "000002"],
                "list_date": ["2000-01-01", None],
            }
        ),
    )
    _write_empty_universe_exclusions(pm=pm, trade_date=trade_date)

    assert Access(pm=pm, processed_version="v1").universe(
        trade_date=trade_date,
        min_listing_calendar_days=30,
    ) == ("000001",)


def test_universe_requires_current_st_and_suspension_objects(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["000001"]}),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "stock_basic",
        pd.DataFrame({"symbol": ["000001"]}),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "suspend_d",
        _empty_symbol_frame(),
    )

    with pytest.raises(FileNotFoundError, match="required Meta"):
        Access(pm=pm, processed_version="v1").universe(
            trade_date=trade_date,
            min_listing_calendar_days=0,
        )


@pytest.mark.parametrize(
    ("value", "error_type"),
    [(True, TypeError), (-1, ValueError)],
)
def test_universe_rejects_invalid_listing_days(
    tmp_path: Path,
    value: object,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        Access(
            pm=PathManager(tmp_path),
            processed_version="v1",
        ).universe(
            trade_date="2026-05-06",
            min_listing_calendar_days=cast(int, value),
        )


def test_level2_universe_uses_level2_base_and_canonical_order(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["000001"]}),
    )
    _write_level2_row_group_fixture(pm=pm, trade_date=trade_date)
    _write_processed_frame(
        pm,
        trade_date,
        "stock_basic",
        pd.DataFrame(
            {
                "symbol": ["000001", "600000", "600001"],
                "list_date": ["2000-01-01", "2000-01-01", None],
            }
        ),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "stock_st",
        pd.DataFrame({"symbol": ["600000"]}),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "suspend_d",
        pd.DataFrame({"symbol": ["600002"]}),
    )

    assert Access(pm=pm, processed_version="v1").level2_universe(
        trade_date=trade_date,
        min_listing_calendar_days=20,
    ) == ("000001",)


def test_trades_preserve_requested_order(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_level2_row_group_fixture(pm=pm, trade_date=trade_date)

    tables = Access(pm=pm, processed_version="v1").trades(
        trade_date=trade_date,
        symbols=["000001", "600001"],
    )

    assert list(tables) == ["000001", "600001"]
    assert tables["000001"]["price"].to_pylist() == [40.0]
    assert tables["600001"]["price"].to_pylist() == [20.0]


def test_trades_read_only_overlapping_row_groups(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_level2_row_group_fixture(pm=pm, trade_date=trade_date)

    original_parquet_file = access_module.pq.ParquetFile
    read_row_group_calls: list[tuple[Path, list[int]]] = []

    class SpyParquetFile:
        def __init__(self, path: str | Path) -> None:
            self.path = Path(path)
            self._inner = original_parquet_file(path)
            self.metadata = self._inner.metadata

        def read_row_groups(self, row_groups: Sequence[int]) -> pa.Table:
            selected = list(row_groups)
            read_row_group_calls.append((self.path, selected))
            return self._inner.read_row_groups(selected)

    monkeypatch.setattr(access_module.pq, "ParquetFile", SpyParquetFile)

    tables = Access(pm=pm, processed_version="v1").trades(
        trade_date=trade_date,
        symbols=["600001"],
    )

    assert tables["600001"]["price"].to_pylist() == [20.0]
    assert read_row_group_calls == [
        (
            pm.processed_data(
                dataset_name="sh_trade",
                version="v1",
                trade_date=trade_date,
            ),
            [1],
        )
    ]


def test_empty_trade_request_does_not_require_level2_objects(
    tmp_path: Path,
) -> None:
    assert (
        Access(
            pm=PathManager(tmp_path),
            processed_version="v1",
        ).trades(
            trade_date="2026-05-06",
            symbols=(),
        )
        == {}
    )


def test_level2_rejects_missing_meta_and_missing_symbol(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    access = Access(pm=pm, processed_version="v1")

    with pytest.raises(FileNotFoundError, match="required Meta"):
        access.level2_universe(
            trade_date=trade_date,
            min_listing_calendar_days=0,
        )

    _write_level2_row_group_fixture(pm=pm, trade_date=trade_date)
    with pytest.raises(KeyError, match="300001"):
        access.trades(
            trade_date=trade_date,
            symbols=["300001"],
        )


def test_level2_rejects_incomplete_parquet_coverage(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_l2_object(
        pm,
        trade_date,
        "sh_trade",
        pd.DataFrame({"symbol": ["600000"], "ts_utc": [1], "price": [10.0]}),
        {"600000": range(0, 2)},
    )
    _write_l2_object(
        pm,
        trade_date,
        "sz_trade",
        pd.DataFrame({"symbol": ["000001"], "ts_utc": [1], "price": [20.0]}),
        {"000001": range(0, 1)},
    )

    with pytest.raises(RuntimeError, match="do not cover parquet rows"):
        Access(pm=pm, processed_version="v1").trades(
            trade_date=trade_date,
            symbols=["600000"],
        )


def test_level2_rejects_cross_dataset_symbol_collision(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    for dataset_name in ("sh_trade", "sz_trade"):
        _write_l2_object(
            pm,
            trade_date,
            dataset_name,
            pd.DataFrame({"symbol": ["000001"], "ts_utc": [1], "price": [20.0]}),
            {"000001": range(0, 1)},
        )

    with pytest.raises(RuntimeError, match="duplicate Level-2 symbol"):
        Access(pm=pm, processed_version="v1").trades(
            trade_date=trade_date,
            symbols=["000001"],
        )


def _empty_symbol_frame() -> pd.DataFrame:
    return pd.DataFrame({"symbol": pd.Series([], dtype="object")})


def _write_empty_universe_exclusions(
    *,
    pm: PathManager,
    trade_date: str,
) -> None:
    _write_processed_frame(
        pm,
        trade_date,
        "stock_st",
        _empty_symbol_frame(),
    )
    _write_processed_frame(
        pm,
        trade_date,
        "suspend_d",
        _empty_symbol_frame(),
    )


def _write_level2_row_group_fixture(
    *,
    pm: PathManager,
    trade_date: str,
) -> None:
    _write_l2_object(
        pm,
        trade_date,
        "sh_trade",
        pd.DataFrame(
            {
                "symbol": [
                    "600000",
                    "600000",
                    "600001",
                    "600002",
                    "600002",
                ],
                "ts_utc": [1, 2, 1, 1, 2],
                "price": [10.0, 10.1, 20.0, 30.0, 30.1],
            }
        ),
        {
            "600000": range(0, 2),
            "600001": range(2, 3),
            "600002": range(3, 5),
        },
        row_group_size=2,
    )
    _write_l2_object(
        pm,
        trade_date,
        "sz_trade",
        pd.DataFrame({"symbol": ["000001"], "ts_utc": [1], "price": [40.0]}),
        {"000001": range(0, 1)},
    )


def _write_processed_frame(
    pm: PathManager,
    trade_date: str,
    dataset_name: str,
    frame: pd.DataFrame,
) -> None:
    path = pm.processed_data(
        dataset_name=dataset_name,
        version="v1",
        trade_date=trade_date,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    meta.commit(
        pm=pm,
        payload_path=path,
    )


def _write_calendar_year(
    pm: PathManager,
    calendar_year: int,
    frame: pd.DataFrame,
) -> None:
    path = pm.processed_year_data(
        dataset_name="trade_calendar",
        version="v1",
        calendar_year=calendar_year,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    meta.commit(
        pm=pm,
        payload_path=path,
    )


def _write_l2_object(
    pm: PathManager,
    trade_date: str,
    dataset_name: str,
    frame: pd.DataFrame,
    symbol_slices: Mapping[str, range],
    row_group_size: int | None = None,
) -> None:
    path = pm.processed_data(
        dataset_name=dataset_name,
        version="v1",
        trade_date=trade_date,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pandas(frame, preserve_index=False),
        path,
        row_group_size=row_group_size,
    )
    meta.commit(
        pm=pm,
        payload_path=path,
        symbol_slices=symbol_slices,
    )
