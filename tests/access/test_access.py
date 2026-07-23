# filepath: tests/access/test_access.py
"""Behavior tests for the user-facing Slice access boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import access as access_module
from src.access import meta
from src.access.access import Slice
from src.utils.path import PathManager


def test_slice_requires_canonical_trade_date(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        Slice(pm, "20260506", version="v1")

    with pytest.raises(TypeError, match="must be a str"):
        Slice(pm, cast(str, 20260506), version="v1")


def test_daily_reads_full_object_and_requested_symbol_order(
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
                "close": [12.0, 20.0, 8.0],
            }
        ),
    )
    market_slice = Slice(pm, trade_date, version="v1")

    complete = market_slice.daily("daily_bar")
    selected = market_slice.daily(
        "daily_bar",
        symbols=["600000", "000001"],
    )
    empty = market_slice.daily("daily_bar", symbols=())

    assert complete["symbol"].tolist() == ["000001", "600000", "000002"]
    assert selected["symbol"].tolist() == ["600000", "000001"]
    assert selected["close"].tolist() == [20.0, 12.0]
    assert empty.empty
    assert empty.columns.tolist() == ["symbol", "close"]


def test_daily_rejects_missing_dataset_and_invalid_symbol_request(
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
    market_slice = Slice(pm, trade_date, version="v1")

    with pytest.raises(FileNotFoundError, match="unknown"):
        market_slice.daily("unknown")
    with pytest.raises(TypeError, match="sequence"):
        market_slice.daily("daily_bar", symbols="000001")
    with pytest.raises(ValueError, match="six-digit"):
        market_slice.daily("daily_bar", symbols=["1"])
    with pytest.raises(ValueError, match="unique"):
        market_slice.daily("daily_bar", symbols=["000001", "000001"])
    with pytest.raises(KeyError, match="600000"):
        market_slice.daily("daily_bar", symbols=["600000"])


def test_daily_rejects_missing_object_and_duplicate_data_identity(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    market_slice = Slice(pm, trade_date, version="v1")

    with pytest.raises(FileNotFoundError, match="daily_bar"):
        market_slice.daily("daily_bar")

    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["000001", "000001"]}),
    )
    with pytest.raises(RuntimeError, match="duplicate symbol"):
        market_slice.daily("daily_bar", symbols=["000001"])


def test_trade_dates_and_daily_window_use_ascending_formal_sessions(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    for trade_date in ("2026-05-04", "2026-05-05", "2026-05-06"):
        _write_processed_frame(
            pm,
            trade_date,
            "daily_bar",
            pd.DataFrame({"symbol": ["000001"]}),
        )
        _write_processed_frame(
            pm,
            trade_date,
            "daily_basic",
            pd.DataFrame(
                {
                    "symbol": ["000001"],
                    "limit_status": [0],
                }
            ),
        )

    payload_only = pm.processed_data(
        dataset_name="daily_bar",
        version="v1",
        trade_date="2026-05-03",
    )
    payload_only.parent.mkdir(parents=True)
    pd.DataFrame({"symbol": ["000001"]}).to_parquet(payload_only, index=False)

    market_slice = Slice(pm, "2026-05-06", version="v1")

    assert market_slice.trade_dates(start_date="2026-05-03") == [
        "2026-05-04",
        "2026-05-05",
        "2026-05-06",
    ]
    assert market_slice.recent_trade_dates(sessions=2) == [
        "2026-05-05",
        "2026-05-06",
    ]
    assert list(market_slice.daily_window("daily_basic", sessions=2)) == [
        "2026-05-05",
        "2026-05-06",
    ]


def test_daily_window_fails_when_any_selected_object_is_missing(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    for trade_date in ("2026-05-05", "2026-05-06"):
        _write_processed_frame(
            pm,
            trade_date,
            "daily_bar",
            pd.DataFrame({"symbol": ["000001"]}),
        )
    _write_processed_frame(
        pm,
        "2026-05-06",
        "daily_basic",
        pd.DataFrame({"symbol": ["000001"], "limit_status": [0]}),
    )

    with pytest.raises(FileNotFoundError, match="daily_basic"):
        Slice(pm, "2026-05-06", version="v1").daily_window(
            "daily_basic",
            sessions=2,
        )


def test_stock_universe_applies_explicit_filters_in_daily_bar_order(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_dates = ("2026-05-04", "2026-05-05", "2026-05-06")
    for trade_date in trade_dates:
        _write_processed_frame(
            pm,
            trade_date,
            "daily_bar",
            pd.DataFrame(
                {
                    "symbol": [
                        "000001",
                        "000002",
                        "000003",
                        "000004",
                        "000005",
                    ]
                }
            ),
        )

    _write_processed_frame(
        pm,
        "2026-05-06",
        "stock_basic",
        pd.DataFrame(
            {
                "symbol": [
                    "000001",
                    "000002",
                    "000003",
                    "000004",
                    "000005",
                ],
                "list_date": [
                    "2000-01-01",
                    "2000-01-01",
                    "2000-01-01",
                    "2000-01-01",
                    "2026-04-20",
                ],
            }
        ),
    )
    _write_processed_frame(
        pm,
        "2026-05-04",
        "stock_st",
        pd.DataFrame({"symbol": ["000002"]}),
    )
    _write_processed_frame(
        pm,
        "2026-05-05",
        "stock_st",
        _empty_symbol_frame(),
    )
    _write_processed_frame(
        pm,
        "2026-05-06",
        "stock_st",
        pd.DataFrame({"symbol": ["000003"]}),
    )
    _write_processed_frame(
        pm,
        "2026-05-06",
        "suspend_d",
        pd.DataFrame({"symbol": ["000004"]}),
    )

    symbols = Slice(pm, "2026-05-06", version="v1").stock_universe(
        min_list_calendar_days=30,
        exclude_st_sessions=3,
        exclude_suspended=True,
    )

    assert symbols == ["000001"]


def test_stock_universe_zero_policies_do_not_require_filter_objects(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["000002", "000001"]}),
    )

    assert Slice(pm, trade_date, version="v1").stock_universe(
        min_list_calendar_days=0,
        exclude_st_sessions=0,
        exclude_suspended=False,
    ) == ["000002", "000001"]


def test_stock_universe_includes_exact_listing_age_boundary(
    tmp_path: Path,
) -> None:
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

    assert Slice(pm, trade_date, version="v1").stock_universe(
        min_list_calendar_days=30,
        exclude_st_sessions=0,
        exclude_suspended=False,
    ) == ["000001"]


def test_stock_universe_rejects_incomplete_st_history(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_bar",
        pd.DataFrame({"symbol": ["000001"]}),
    )

    with pytest.raises(RuntimeError, match="insufficient daily_bar history"):
        Slice(pm, trade_date, version="v1").stock_universe(
            min_list_calendar_days=0,
            exclude_st_sessions=2,
            exclude_suspended=False,
        )


def test_stock_universe_rejects_missing_required_st_object(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    for trade_date in ("2026-05-05", "2026-05-06"):
        _write_processed_frame(
            pm,
            trade_date,
            "daily_bar",
            pd.DataFrame({"symbol": ["000001"]}),
        )
    _write_processed_frame(
        pm,
        "2026-05-06",
        "stock_st",
        _empty_symbol_frame(),
    )

    with pytest.raises(FileNotFoundError, match="stock_st"):
        Slice(pm, "2026-05-06", version="v1").stock_universe(
            min_list_calendar_days=0,
            exclude_st_sessions=2,
            exclude_suspended=False,
        )


@pytest.mark.parametrize(
    ("keyword", "value", "error_type"),
    [
        ("min_list_calendar_days", True, TypeError),
        ("min_list_calendar_days", -1, ValueError),
        ("exclude_st_sessions", True, TypeError),
        ("exclude_st_sessions", -1, ValueError),
        ("exclude_suspended", 1, TypeError),
    ],
)
def test_stock_universe_rejects_invalid_policy_values(
    tmp_path: Path,
    keyword: str,
    value: object,
    error_type: type[Exception],
) -> None:
    values: dict[str, object] = {
        "min_list_calendar_days": 0,
        "exclude_st_sessions": 0,
        "exclude_suspended": False,
    }
    values[keyword] = value

    with pytest.raises(error_type):
        Slice(
            PathManager(tmp_path),
            "2026-05-06",
            version="v1",
        ).stock_universe(
            min_list_calendar_days=cast(
                int,
                values["min_list_calendar_days"],
            ),
            exclude_st_sessions=cast(int, values["exclude_st_sessions"]),
            exclude_suspended=cast(bool, values["exclude_suspended"]),
        )


def test_closed_limit_up_symbols_includes_regular_and_one_price_limit_up(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_basic",
        pd.DataFrame(
            {
                "symbol": [
                    "000001",
                    "000002",
                    "000003",
                    "000004",
                    "000005",
                    "000006",
                    "000007",
                ],
                "limit_status": pd.Series(range(7), dtype="int64"),
            }
        ),
    )

    assert Slice(pm, trade_date, version="v1").closed_limit_up_symbols() == [
        "000003",
        "000004",
    ]


@pytest.mark.parametrize(
    "limit_status",
    [
        pd.Series([2.0], dtype="float64"),
        pd.Series(["2"], dtype="object"),
        pd.Series([7], dtype="int64"),
        pd.Series([None], dtype="Int64"),
        pd.Series([True], dtype="bool"),
    ],
)
def test_closed_limit_up_symbols_rejects_invalid_status(
    tmp_path: Path,
    limit_status: pd.Series,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_processed_frame(
        pm,
        trade_date,
        "daily_basic",
        pd.DataFrame(
            {
                "symbol": ["000001"],
                "limit_status": limit_status,
            }
        ),
    )

    with pytest.raises(ValueError, match="limit_status"):
        Slice(pm, trade_date, version="v1").closed_limit_up_symbols()


def test_level2_symbols_and_reads_preserve_requested_order(
    tmp_path: Path,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"
    _write_level2_row_group_fixture(pm=pm, trade_date=trade_date)
    market_slice = Slice(pm, trade_date, version="v1")

    assert market_slice.level2_symbols() == [
        "600000",
        "600001",
        "600002",
        "000001",
    ]

    tables = market_slice.level2(["000001", "600001"])

    assert list(tables) == ["000001", "600001"]
    assert tables["000001"]["price"].to_pylist() == [40.0]
    assert tables["600001"]["price"].to_pylist() == [20.0]


def test_level2_reads_only_overlapping_row_groups(
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

    tables = Slice(pm, trade_date, version="v1").level2(["600001"])

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


def test_level2_empty_request_does_not_require_level2_objects(
    tmp_path: Path,
) -> None:
    assert (
        Slice(
            PathManager(tmp_path),
            "2026-05-06",
            version="v1",
        ).level2(())
        == {}
    )


def test_level2_rejects_missing_meta_and_missing_symbol(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-06"

    with pytest.raises(FileNotFoundError, match="unavailable"):
        Slice(pm, trade_date, version="v1").level2_symbols()

    _write_level2_row_group_fixture(pm=pm, trade_date=trade_date)
    with pytest.raises(KeyError, match="300001"):
        Slice(pm, trade_date, version="v1").level2(["300001"])


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
        Slice(pm, trade_date, version="v1").level2(["600000"])


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
        Slice(pm, trade_date, version="v1").level2_symbols()


def _empty_symbol_frame() -> pd.DataFrame:
    return pd.DataFrame({"symbol": pd.Series([], dtype="object")})


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
    meta.write(
        payload_path=path,
        storage_root=pm.storage_root,
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
    meta.write(
        payload_path=path,
        storage_root=pm.storage_root,
        symbol_slices=symbol_slices,
    )
