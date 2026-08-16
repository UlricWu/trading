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
from src.utils import table_ops
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager

__all__ = ("Access",)


class Access:
    """Provide table-independent access to one processed market-data version.

    Example:
        from pathlib import Path

        from src.access import Access
        from src.utils.path import PathManager

        pm = PathManager(Path("/absolute/path/to/formal-storage"))
        access = Access(pm=pm, processed_version="v1")
        symbols = access.universe(
            trade_date="2026-05-06",
            min_listing_calendar_days=20,
        )
        bars = access.daily_bars(
            trade_date="2026-05-06",
            symbols=symbols,
        )
    """

    _LEVEL2_DATASETS: ClassVar[tuple[str, ...]] = ("sh_trade", "sz_trade")

    def __init__(
        self,
        pm: PathManager,
        *,
        processed_version: str,
    ) -> None:
        """Bind one formal storage layout and processed data version.

        Example:
            access = Access(pm=pm, processed_version="v1")
        """
        self._pm = pm
        self._processed_version = PathManager.require_safe_basename(
            processed_version,
            "processed_version",
        )

    def trade_dates(
        self,
        *,
        start_date: str,
        end_date: str,
    ) -> list[str]:
        """Return formal open dates in the complete calendar interval.

        Example:
            for trade_date in access.trade_dates(
                start_date="2026-05-01",
                end_date="2026-05-31",
            ):
                bars = access.daily_bars(trade_date=trade_date)
        """
        validated_start = DateTimeUtils.require_system_date(
            start_date,
            field_name="start_date",
        )
        validated_end = DateTimeUtils.require_system_date(
            end_date,
            field_name="end_date",
        )
        if validated_start > validated_end:
            raise ValueError(
                f"invalid date range: start={validated_start}, end={validated_end}"
            )

        trade_dates: list[str] = []
        for calendar_year in range(
            int(validated_start[:4]),
            int(validated_end[:4]) + 1,
        ):
            calendar = self._read_calendar_year(calendar_year)
            requested_rows = calendar["trade_date"].between(
                validated_start,
                validated_end,
            )
            trade_dates.extend(
                calendar.loc[
                    requested_rows & calendar["is_open"],
                    "trade_date",
                ].tolist()
            )
        return sorted(trade_dates)

    def recent_trade_dates(
        self,
        *,
        end_date: str,
        sessions: int,
    ) -> list[str]:
        """Return the latest formal sessions ending at the requested date.

        Example:
            history_dates = access.recent_trade_dates(
                end_date="2026-05-29",
                sessions=20,
            )
        """
        validated_end = DateTimeUtils.require_system_date(
            end_date,
            field_name="end_date",
        )
        if not isinstance(sessions, int) or isinstance(sessions, bool):
            raise TypeError("sessions must be an int")
        if sessions <= 0:
            raise ValueError("sessions must be positive")

        version_dir = self._pm.processed_version_dir(
            dataset_name="trade_calendar",
            version=self._processed_version,
        )
        if not version_dir.is_dir():
            raise FileNotFoundError(
                f"formal trade-calendar version is unavailable: {version_dir}"
            )

        partition_prefix = "year="
        calendar_years: list[int] = []
        for partition_dir in version_dir.iterdir():
            if (
                not partition_dir.is_dir()
                or not partition_dir.name.startswith(partition_prefix)
                or not (partition_dir / "meta.json").is_file()
            ):
                continue
            year_text = partition_dir.name.removeprefix(partition_prefix)
            if len(year_text) == 4 and year_text.isdigit():
                calendar_year = int(year_text)
                if calendar_year <= int(validated_end[:4]):
                    calendar_years.append(calendar_year)

        end_year = int(validated_end[:4])
        if end_year not in calendar_years:
            raise FileNotFoundError(
                f"formal trade calendar is unavailable: trade_date={validated_end}"
            )

        selected: list[str] = []
        for calendar_year in range(end_year, min(calendar_years) - 1, -1):
            calendar = self._read_calendar_year(calendar_year)
            if calendar_year == end_year:
                calendar = calendar.loc[calendar["trade_date"] <= validated_end]
            open_dates = sorted(
                calendar.loc[calendar["is_open"], "trade_date"].tolist()
            )
            if calendar_year == end_year and validated_end not in open_dates:
                raise ValueError(
                    f"end_date is not a formal trade date: end_date={validated_end}"
                )

            missing_sessions = sessions - len(selected)
            selected = open_dates[-missing_sessions:] + selected
            if len(selected) == sessions:
                return selected

        raise RuntimeError(
            f"insufficient trade-calendar history: "
            f"trade_date={validated_end}, required={sessions}, "
            f"available={len(selected)}"
        )

    def universe(
        self,
        *,
        trade_date: str,
        min_listing_calendar_days: int,
    ) -> tuple[str, ...]:
        """Return the filtered daily-bar universe in canonical symbol order.

        Example:
            symbols = access.universe(
                trade_date="2026-05-06",
                min_listing_calendar_days=20,
            )
            bars = access.daily_bars(
                trade_date="2026-05-06",
                symbols=symbols,
            )
        """
        validated_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )
        _require_non_negative_int(
            min_listing_calendar_days,
            field_name="min_listing_calendar_days",
        )
        daily_bars = self._read_processed_frame(
            trade_date=validated_date,
            dataset_name="daily_bar",
        )
        symbols = _unique_data_symbols(
            daily_bars,
            dataset_name="daily_bar",
        )
        return self._filter_universe_symbols(
            symbols=symbols,
            trade_date=validated_date,
            min_listing_calendar_days=min_listing_calendar_days,
        )

    def level2_universe(
        self,
        *,
        trade_date: str,
        min_listing_calendar_days: int,
    ) -> tuple[str, ...]:
        """Return filtered symbols with complete Level-2 data for one date.

        Example:
            symbols = access.level2_universe(
                trade_date="2026-05-06",
                min_listing_calendar_days=20,
            )
            selected_trades = access.trades(
                trade_date="2026-05-06",
                symbols=symbols[:100],
            )
        """
        validated_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )
        _require_non_negative_int(
            min_listing_calendar_days,
            field_name="min_listing_calendar_days",
        )
        symbols = [
            symbol
            for symbol_slices in self._load_level2_index(
                trade_date=validated_date
            ).values()
            for symbol in symbol_slices
        ]
        return self._filter_universe_symbols(
            symbols=symbols,
            trade_date=validated_date,
            min_listing_calendar_days=min_listing_calendar_days,
        )

    def daily_bars(
        self,
        *,
        trade_date: str,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """Return formal daily bars, optionally in requested symbol order.

        Example:
            all_bars = access.daily_bars(trade_date="2026-05-06")
            selected_bars = access.daily_bars(
                trade_date="2026-05-06",
                symbols=("000001", "600000"),
            )
        """
        validated_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )
        requested_symbols = None
        if symbols is not None:
            requested_symbols = _validated_symbols(symbols)

        frame = self._read_processed_frame(
            trade_date=validated_date,
            dataset_name="daily_bar",
        )
        if requested_symbols is None:
            return frame

        available_symbols = _unique_data_symbols(
            frame,
            dataset_name="daily_bar",
        )
        missing_symbols = set(requested_symbols) - set(available_symbols)
        if missing_symbols:
            raise KeyError(
                f"symbols not found in daily bars: {sorted(missing_symbols)}"
            )
        if not requested_symbols:
            return frame.iloc[0:0].copy()

        return (
            frame.set_index("symbol", drop=False)
            .loc[requested_symbols]
            .reset_index(drop=True)
        )

    def trades(
        self,
        *,
        trade_date: str,
        symbols: Sequence[str],
    ) -> dict[str, pa.Table]:
        """Return requested Level-2 trades in request order.

        Example:
            trades_by_symbol = access.trades(
                trade_date="2026-05-06",
                symbols=("600000", "000001"),
            )
            sh_trades = trades_by_symbol["600000"]
        """
        validated_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )
        requested_symbols = _validated_symbols(symbols)
        if not requested_symbols:
            return {}

        level2_index = self._load_level2_index(trade_date=validated_date)
        path_by_symbol = {
            symbol: output_path
            for output_path, symbol_slices in level2_index.items()
            for symbol in symbol_slices
        }
        missing_symbols = set(requested_symbols) - set(path_by_symbol)
        if missing_symbols:
            raise KeyError(
                f"symbols not found in Level-2 trades: {sorted(missing_symbols)}"
            )

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

    def _filter_universe_symbols(
        self,
        *,
        symbols: Sequence[str],
        trade_date: str,
        min_listing_calendar_days: int,
    ) -> tuple[str, ...]:
        selected_symbols = list(symbols)
        if min_listing_calendar_days > 0:
            stock_basic = self._read_processed_frame(
                trade_date=trade_date,
                dataset_name="stock_basic",
            )
            table_ops.require_columns(
                stock_basic,
                ("symbol", "list_date"),
                who="stock_basic",
            )
            minimum_list_date = DateTimeUtils.days_before(
                trade_date,
                min_listing_calendar_days,
                field_name="trade_date",
            )
            list_dates = stock_basic["list_date"]
            listing_eligible_symbols = set(
                stock_basic.loc[
                    list_dates.notna() & list_dates.le(minimum_list_date),
                    "symbol",
                ].tolist()
            )
            selected_symbols = [
                symbol
                for symbol in selected_symbols
                if symbol in listing_eligible_symbols
            ]

        stock_st = self._read_processed_frame(
            trade_date=trade_date,
            dataset_name="stock_st",
        )
        st_symbols = set(
            _unique_data_symbols(
                stock_st,
                dataset_name="stock_st",
            )
        )
        suspend_d = self._read_processed_frame(
            trade_date=trade_date,
            dataset_name="suspend_d",
        )
        suspended_symbols = set(
            _unique_data_symbols(
                suspend_d,
                dataset_name="suspend_d",
            )
        )
        return tuple(
            sorted(
                symbol
                for symbol in selected_symbols
                if symbol not in st_symbols and symbol not in suspended_symbols
            )
        )

    def _read_processed_frame(
        self,
        *,
        trade_date: str,
        dataset_name: str,
    ) -> pd.DataFrame:
        loaded = self._load_processed_meta(
            trade_date=trade_date,
            dataset_name=dataset_name,
        )
        return pq.ParquetFile(loaded.payload_path).read().to_pandas()

    def _read_calendar_year(self, calendar_year: int) -> pd.DataFrame:
        meta_path = self._pm.processed_year_meta(
            dataset_name="trade_calendar",
            version=self._processed_version,
            calendar_year=calendar_year,
        )
        output_path = self._pm.processed_year_data(
            dataset_name="trade_calendar",
            version=self._processed_version,
            calendar_year=calendar_year,
        )
        loaded = meta.require(
            pm=self._pm,
            meta_path=meta_path,
            expected_payload_path=output_path,
        )
        return pq.ParquetFile(loaded.payload_path).read().to_pandas()

    def _load_processed_meta(
        self,
        *,
        trade_date: str,
        dataset_name: str,
    ) -> meta.MetaRecord:
        meta_path = self._pm.processed_meta(
            dataset_name=dataset_name,
            version=self._processed_version,
            trade_date=trade_date,
        )
        output_path = self._pm.processed_data(
            dataset_name=dataset_name,
            version=self._processed_version,
            trade_date=trade_date,
        )
        return meta.require(
            pm=self._pm,
            meta_path=meta_path,
            expected_payload_path=output_path,
        )

    def _load_level2_index(
        self,
        *,
        trade_date: str,
    ) -> dict[Path, Mapping[str, range]]:
        level2_index: dict[Path, Mapping[str, range]] = {}
        seen_symbols: set[str] = set()
        for dataset_name in self._LEVEL2_DATASETS:
            loaded = self._load_processed_meta(
                trade_date=trade_date,
                dataset_name=dataset_name,
            )
            if loaded.symbol_slices is None:
                raise RuntimeError(
                    f"Level-2 Meta has no symbol_slices: "
                    f"trade_date={trade_date}, dataset={dataset_name}, "
                    f"payload={loaded.payload_path}"
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
                    f"trade_date={trade_date}, symbols={sorted(duplicates)}"
                )
            seen_symbols.update(loaded.symbol_slices)
            level2_index[loaded.payload_path] = loaded.symbol_slices
        return level2_index


def _validated_symbols(symbols: Sequence[str]) -> list[str]:
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


def _unique_data_symbols(
    frame: pd.DataFrame,
    *,
    dataset_name: str,
) -> list[str]:
    table_ops.require_nonempty_strings(frame, ("symbol",), who=dataset_name)
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
    table_ops.require_unique(frame, ("symbol",), who=dataset_name)
    return symbols


def _require_non_negative_int(value: int, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an int")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
