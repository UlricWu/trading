# filepath: src/access/access.py
"""Research-facing access to formal processed market data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.access import meta
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager

__all__ = ("Slice",)


class Slice:
    """Research-facing view of one formal processed market-data date."""

    _LEVEL2_DATASETS: ClassVar[tuple[str, ...]] = ("sh_trade", "sz_trade")

    def __init__(
        self,
        pm: PathManager,
        trade_date: str,
        *,
        version: str,
    ) -> None:
        """Bind one storage layout, processed version, and trade date."""
        self._pm = pm
        self._trade_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )
        self._version = PathManager.require_safe_basename(version, "version")

    def trade_dates(self, *, start_date: str) -> list[str]:
        """Return formal daily-bar dates from `start_date` through this slice."""
        validated_start = DateTimeUtils.require_system_date(
            start_date,
            field_name="start_date",
        )
        if validated_start > self._trade_date:
            raise ValueError(
                f"invalid date range: start={validated_start}, end={self._trade_date}"
            )

        dates = self._daily_bar_meta_dates(start_date=validated_start)
        for trade_date in dates:
            self._load_processed_meta(
                trade_date=trade_date,
                dataset_name="daily_bar",
            )
        return dates

    def recent_trade_dates(self, *, sessions: int) -> list[str]:
        """Return the latest formal sessions ending at this slice date."""
        if not isinstance(sessions, int) or isinstance(sessions, bool):
            raise TypeError("sessions must be an int")
        if sessions <= 0:
            raise ValueError("sessions must be positive")
        dates = self._daily_bar_meta_dates(start_date=None)
        if self._trade_date not in dates:
            raise FileNotFoundError(
                f"formal daily_bar is unavailable: trade_date={self._trade_date}"
            )
        if len(dates) < sessions:
            raise RuntimeError(
                f"insufficient daily_bar history: "
                f"trade_date={self._trade_date}, required={sessions}, "
                f"available={len(dates)}"
            )

        selected = dates[-sessions:]
        for trade_date in selected:
            self._load_processed_meta(
                trade_date=trade_date,
                dataset_name="daily_bar",
            )
        return selected

    def daily(
        self,
        dataset_name: str,
        *,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """Return one formal daily dataset, optionally ordered by symbol."""
        requested_symbols = None
        if symbols is not None:
            requested_symbols = _validated_symbols(symbols)

        frame = self._read_daily_dataset(
            trade_date=self._trade_date,
            dataset_name=dataset_name,
        )
        if requested_symbols is None:
            return frame

        available_symbols = _unique_data_symbols(
            frame,
            dataset_name=dataset_name,
        )
        missing_symbols = set(requested_symbols) - set(available_symbols)
        if missing_symbols:
            raise KeyError(
                f"symbols not found in {dataset_name}: {sorted(missing_symbols)}"
            )

        if not requested_symbols:
            return frame.iloc[0:0].copy()

        return (
            frame.set_index("symbol", drop=False)
            .loc[requested_symbols]
            .reset_index(drop=True)
        )

    def daily_window(
        self,
        dataset_name: str,
        *,
        sessions: int,
    ) -> dict[str, pd.DataFrame]:
        """Return complete daily objects for an ascending formal-date window."""
        trade_dates = self.recent_trade_dates(sessions=sessions)
        return {
            trade_date: self._read_daily_dataset(
                trade_date=trade_date,
                dataset_name=dataset_name,
            )
            for trade_date in trade_dates
        }

    def stock_universe(
        self,
        *,
        min_list_calendar_days: int,
        exclude_st_sessions: int,
        exclude_suspended: bool,
    ) -> list[str]:
        """Return the filtered daily stock universe in daily-bar order."""
        _require_non_negative_int(
            min_list_calendar_days,
            field_name="min_list_calendar_days",
        )
        _require_non_negative_int(
            exclude_st_sessions,
            field_name="exclude_st_sessions",
        )
        if type(exclude_suspended) is not bool:
            raise TypeError("exclude_suspended must be a bool")

        daily_bar = self.daily("daily_bar")
        symbols = _unique_data_symbols(daily_bar, dataset_name="daily_bar")

        if min_list_calendar_days > 0:
            stock_basic = self.daily("stock_basic")
            _require_columns(
                stock_basic,
                ("symbol", "list_date"),
                dataset_name="stock_basic",
            )
            listed_symbols = _unique_data_symbols(
                stock_basic,
                dataset_name="stock_basic",
            )
            missing_symbols = set(symbols) - set(listed_symbols)
            if missing_symbols:
                raise KeyError(
                    "daily_bar symbols not found in stock_basic: "
                    f"{sorted(missing_symbols)}"
                )

            list_date_by_symbol = dict(
                zip(
                    listed_symbols,
                    stock_basic["list_date"].tolist(),
                    strict=True,
                )
            )
            minimum_list_date = DateTimeUtils.days_before(
                self._trade_date,
                min_list_calendar_days,
                field_name="trade_date",
            )
            symbols = [
                symbol
                for symbol in symbols
                if DateTimeUtils.require_system_date(
                    list_date_by_symbol[symbol],
                    field_name=f"stock_basic.list_date[{symbol}]",
                )
                <= minimum_list_date
            ]

        if exclude_st_sessions > 0:
            recent_st_symbols: set[str] = set()
            for trade_date in self.recent_trade_dates(
                sessions=exclude_st_sessions,
            ):
                stock_st = self._read_daily_dataset(
                    trade_date=trade_date,
                    dataset_name="stock_st",
                )
                recent_st_symbols.update(
                    _unique_data_symbols(
                        stock_st,
                        dataset_name=f"stock_st[{trade_date}]",
                    )
                )
            symbols = [symbol for symbol in symbols if symbol not in recent_st_symbols]

        if exclude_suspended:
            suspend_d = self.daily("suspend_d")
            suspended_symbols = set(
                _unique_data_symbols(
                    suspend_d,
                    dataset_name="suspend_d",
                )
            )
            symbols = [symbol for symbol in symbols if symbol not in suspended_symbols]

        return symbols

    def closed_limit_up_symbols(self) -> list[str]:
        """Return symbols whose provider status is closed limit-up."""
        daily_basic = self.daily("daily_basic")
        _require_columns(
            daily_basic,
            ("symbol", "limit_status"),
            dataset_name="daily_basic",
        )
        symbols = _unique_data_symbols(
            daily_basic,
            dataset_name="daily_basic",
        )
        limit_status = daily_basic["limit_status"]
        if (
            not pd.api.types.is_integer_dtype(limit_status.dtype)
            or pd.api.types.is_bool_dtype(limit_status.dtype)
            or limit_status.isna().any()
            or not limit_status.isin(range(7)).all()
        ):
            raise ValueError(
                "daily_basic.limit_status must contain non-null integers in 0..6"
            )

        return [
            symbol
            for symbol, status in zip(
                symbols,
                limit_status.tolist(),
                strict=True,
            )
            if status in (2, 3)
        ]

    def level2_symbols(self) -> list[str]:
        """Return symbols with complete Level-2 slices for this trade date."""
        return [
            symbol
            for symbol_slices in self._load_level2_index().values()
            for symbol in symbol_slices
        ]

    def level2(self, symbols: Sequence[str]) -> dict[str, pa.Table]:
        """Return requested Level-2 symbol slices in request order."""
        requested_symbols = _validated_symbols(symbols)
        if not requested_symbols:
            return {}

        level2_index = self._load_level2_index()
        path_by_symbol = {
            symbol: output_path
            for output_path, symbol_slices in level2_index.items()
            for symbol in symbol_slices
        }
        missing_symbols = set(requested_symbols) - set(path_by_symbol)
        if missing_symbols:
            raise KeyError(f"symbols not found in Level-2: {sorted(missing_symbols)}")

        slices_by_path: dict[Path, dict[str, range]] = {}
        for symbol in requested_symbols:
            output_path = path_by_symbol[symbol]
            slices_by_path.setdefault(output_path, {})[symbol] = level2_index[
                output_path
            ][symbol]

        tables: dict[str, pa.Table] = {}
        for output_path, symbol_slices in slices_by_path.items():
            parquet_file = pq.ParquetFile(output_path)
            row_group_bounds: list[range] = []
            row_start = 0
            for row_group_id in range(parquet_file.metadata.num_row_groups):
                row_group = parquet_file.metadata.row_group(row_group_id)
                row_end = row_start + row_group.num_rows
                row_group_bounds.append(range(row_start, row_end))
                row_start = row_end

            row_groups_by_symbol: dict[str, list[int]] = {}
            selected_row_groups: set[int] = set()
            for symbol, rows in symbol_slices.items():
                overlapping_row_groups = [
                    row_group_id
                    for row_group_id, row_group_rows in enumerate(row_group_bounds)
                    if row_group_rows.stop > rows.start
                    and row_group_rows.start < rows.stop
                ]
                row_groups_by_symbol[symbol] = overlapping_row_groups
                selected_row_groups.update(overlapping_row_groups)

            ordered_row_groups = sorted(selected_row_groups)
            selected_offsets: dict[int, int] = {}
            selected_start = 0
            for row_group_id in ordered_row_groups:
                selected_offsets[row_group_id] = selected_start
                selected_start += len(row_group_bounds[row_group_id])
            selected_table = parquet_file.read_row_groups(ordered_row_groups)

            for symbol, rows in symbol_slices.items():
                first_row_group_id = row_groups_by_symbol[symbol][0]
                first_row_group_start = row_group_bounds[first_row_group_id].start
                local_start = (
                    selected_offsets[first_row_group_id]
                    + rows.start
                    - first_row_group_start
                )
                tables[symbol] = selected_table.slice(local_start, len(rows))

        return {symbol: tables[symbol] for symbol in requested_symbols}

    def _read_daily_dataset(
        self,
        *,
        trade_date: str,
        dataset_name: str,
    ) -> pd.DataFrame:
        """Read one formal daily processed payload."""
        loaded = self._load_processed_meta(
            trade_date=trade_date,
            dataset_name=dataset_name,
        )
        return pq.ParquetFile(loaded.payload_path).read().to_pandas()

    def _daily_bar_meta_dates(
        self,
        *,
        start_date: str | None,
    ) -> list[str]:
        """Return dates with committed daily-bar Meta through this slice."""
        version_dir = self._pm.processed_version_dir(
            dataset_name="daily_bar",
            version=self._version,
        )
        if not version_dir.is_dir():
            raise FileNotFoundError(
                f"daily_bar version directory is unavailable: {version_dir}"
            )

        meta_dates: list[str] = []
        partition_prefix = "trade_date="
        for partition_dir in version_dir.iterdir():
            if not partition_dir.is_dir() or not partition_dir.name.startswith(
                partition_prefix
            ):
                continue
            if not (partition_dir / "meta.json").is_file():
                continue
            trade_date = DateTimeUtils.require_system_date(
                partition_dir.name.removeprefix(partition_prefix),
                field_name="daily_bar partition trade_date",
            )
            if trade_date > self._trade_date:
                continue
            if start_date is not None and trade_date < start_date:
                continue
            meta_dates.append(trade_date)

        return sorted(meta_dates)

    def _load_processed_meta(
        self,
        *,
        trade_date: str,
        dataset_name: str,
    ) -> meta.MetaRecord:
        """Return one required formal processed object."""
        meta_path = self._pm.processed_meta(
            dataset_name=dataset_name,
            version=self._version,
            trade_date=trade_date,
        )
        output_path = self._pm.processed_data(
            dataset_name=dataset_name,
            version=self._version,
            trade_date=trade_date,
        )
        loaded = meta.load(
            meta_path=meta_path,
            storage_root=self._pm.storage_root,
            expected_payload_path=output_path,
        )
        if loaded is None:
            raise FileNotFoundError(
                f"formal processed dataset is unavailable: "
                f"dataset={dataset_name}, trade_date={trade_date}, "
                f"meta_path={meta_path}"
            )
        return loaded

    def _load_level2_index(self) -> dict[Path, Mapping[str, range]]:
        """Return validated Level-2 indexes grouped by payload."""
        level2_index: dict[Path, Mapping[str, range]] = {}
        seen_symbols: set[str] = set()
        for dataset_name in self._LEVEL2_DATASETS:
            loaded = self._load_processed_meta(
                trade_date=self._trade_date,
                dataset_name=dataset_name,
            )
            if loaded.symbol_slices is None:
                raise RuntimeError(
                    f"Level-2 Meta has no symbol_slices: "
                    f"trade_date={self._trade_date}, "
                    f"dataset={dataset_name}, payload={loaded.payload_path}"
                )

            parquet_file = pq.ParquetFile(loaded.payload_path)
            total_rows = parquet_file.metadata.num_rows
            final_end = max(rows.stop for rows in loaded.symbol_slices.values())
            if final_end != total_rows:
                raise RuntimeError(
                    f"symbol slices do not cover parquet rows: "
                    f"dataset={dataset_name}, final_end={final_end}, "
                    f"rows={total_rows}, output_path={loaded.payload_path}"
                )
            duplicates = seen_symbols.intersection(loaded.symbol_slices)
            if duplicates:
                raise RuntimeError(
                    f"duplicate Level-2 symbols across datasets: "
                    f"trade_date={self._trade_date}, "
                    f"symbols={sorted(duplicates)}"
                )
            seen_symbols.update(loaded.symbol_slices)
            level2_index[loaded.payload_path] = loaded.symbol_slices

        return level2_index


def _validated_symbols(symbols: Sequence[str]) -> list[str]:
    """Copy and validate one public symbol request."""
    if isinstance(symbols, str):
        raise TypeError("symbols must be a sequence of six-digit strings")

    requested_symbols = list(symbols)
    invalid_symbols = [
        symbol
        for symbol in requested_symbols
        if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isdigit()
    ]
    if invalid_symbols:
        raise ValueError(f"symbols must be six-digit strings: {invalid_symbols!r}")
    if len(requested_symbols) != len(set(requested_symbols)):
        raise ValueError("symbols must be unique")
    return requested_symbols


def _require_columns(
    frame: pd.DataFrame,
    columns: Sequence[str],
    *,
    dataset_name: str,
) -> None:
    """Reject a processed dataset missing required Access fields."""
    missing_columns = [column for column in columns if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"{dataset_name} missing required columns: {missing_columns}")


def _unique_data_symbols(
    frame: pd.DataFrame,
    *,
    dataset_name: str,
) -> list[str]:
    """Return a validated unique processed symbol column."""
    _require_columns(frame, ("symbol",), dataset_name=dataset_name)
    symbols = frame["symbol"].tolist()
    invalid_symbols = [
        symbol
        for symbol in symbols
        if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isdigit()
    ]
    if invalid_symbols:
        raise ValueError(
            f"{dataset_name}.symbol must contain six-digit strings: {invalid_symbols!r}"
        )
    if len(symbols) != len(set(symbols)):
        raise RuntimeError(f"{dataset_name} contains duplicate symbol identities")
    return symbols


def _require_non_negative_int(value: int, *, field_name: str) -> None:
    """Validate an explicit non-negative integer policy."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an int")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
