# filepath: src/data_system/builders/label_daily_forward_excess_rank.py
"""Daily forward adjusted-close excess rank label builder."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.access import Access, meta
from src.utils import table_ops
from src.data_system.builders.base import InputSpec
from src.utils.path import PathManager


_HORIZONS = (1, 3, 5)

_REQUIRED_COLUMNS = (
    "symbol",
    "trade_date",
    "close",
    "adj_factor",
)

_OUTPUT_COLUMNS = MappingProxyType(
    {
        "exit_date_d1": 1,
        "exit_date_d3": 3,
        "exit_date_d5": 5,
        "future_return_d1": 1,
        "future_return_d3": 3,
        "future_return_d5": 5,
        "future_benchmark_return_d1": 1,
        "future_benchmark_return_d3": 3,
        "future_benchmark_return_d5": 5,
        "future_excess_return_d1": 1,
        "future_excess_return_d3": 3,
        "future_excess_return_d5": 5,
        "y_rank_excess_return_d1": 1,
        "y_rank_excess_return_d3": 3,
        "y_rank_excess_return_d5": 5,
        "label_valid_d1": 1,
        "label_valid_d3": 3,
        "label_valid_d5": 5,
        "mask_reason_d1": 1,
        "mask_reason_d3": 3,
        "mask_reason_d5": 5,
    }
)


class DailyForwardExcessRankV1Builder:
    """Build v1 forward excess-return rank labels.

    Example:
        builder = DailyForwardExcessRankV1Builder()
        labels = builder.build_partition(input_table)
    """

    key_columns = (
        "symbol",
        "trade_date",
    )
    output_columns = tuple(_OUTPUT_COLUMNS)

    @property
    def lookahead(self) -> int:
        return max(_HORIZONS)

    def target_lookahead(self, label_column: str) -> int:
        try:
            return _OUTPUT_COLUMNS[label_column]
        except KeyError as exc:
            raise ValueError(f"unknown label_column: {label_column!r}") from exc

    def read_input(
        self,
        *,
        pm: PathManager,
        trade_dates: Sequence[str],
    ) -> pa.Table:
        """Read the complete six-session input window.

        Example:
            table = builder.read_input(
                pm=path_manager,
                trade_dates=(
                    "2026-07-13",
                    "2026-07-14",
                    "2026-07-15",
                    "2026-07-16",
                    "2026-07-17",
                    "2026-07-20",
                ),
            )
        """
        return _read_window(
            pm=pm,
            trade_dates=trade_dates,
        )

    def build_partition(
        self,
        table: pa.Table | Mapping[InputSpec, pa.Table],
    ) -> pa.Table:
        """Build one signal-date label partition.

        Example:
            labels = DailyForwardExcessRankV1Builder().build_partition(
                input_table
            )
        """
        if not isinstance(table, pa.Table):
            raise TypeError("daily label input must be a pyarrow.Table")
        table_ops.require_columns(
            table,
            _REQUIRED_COLUMNS,
            who="daily_forward_excess_rank input",
        )

        df = table.select(_REQUIRED_COLUMNS).to_pandas()
        dates = sorted(df["trade_date"].dropna().unique().tolist())
        if len(dates) < self.lookahead + 1:
            raise ValueError(
                "daily_forward_excess_rank requires 6 trade_date partitions"
            )

        window_dates = dates[: self.lookahead + 1]
        signal_date = window_dates[0]
        frame = _signal_frame(df=df, signal_date=signal_date)

        for horizon in _HORIZONS:
            exit_date = window_dates[horizon]
            frame[f"exit_date_d{horizon}"] = exit_date
            frame = _append_horizon_label(
                frame=frame,
                source=df,
                horizon=horizon,
                exit_date=exit_date,
            )

        result = frame[[*self.key_columns, *self.output_columns]].sort_values(
            list(self.key_columns)
        )
        return pa.Table.from_pandas(result, preserve_index=False)


def _signal_frame(*, df: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    signal = df[df["trade_date"] == signal_date][
        ["symbol", "trade_date", "close", "adj_factor"]
    ].copy()
    signal = signal.rename(
        columns={
            "close": "signal_close",
            "adj_factor": "signal_adj_factor",
        }
    )
    signal["signal_close"] = pd.to_numeric(signal["signal_close"], errors="coerce")
    signal["signal_adj_factor"] = pd.to_numeric(
        signal["signal_adj_factor"],
        errors="coerce",
    )
    signal["signal_adjusted_close"] = (
        signal["signal_close"] * signal["signal_adj_factor"]
    )
    return signal


def _append_horizon_label(
    *,
    frame: pd.DataFrame,
    source: pd.DataFrame,
    horizon: int,
    exit_date: str,
) -> pd.DataFrame:
    suffix = f"d{horizon}"
    exit_frame = source[source["trade_date"] == exit_date][
        ["symbol", "close", "adj_factor"]
    ].copy()
    exit_frame = exit_frame.rename(
        columns={
            "close": f"exit_close_{suffix}",
            "adj_factor": f"exit_adj_factor_{suffix}",
        }
    )
    exit_frame[f"exit_close_{suffix}"] = pd.to_numeric(
        exit_frame[f"exit_close_{suffix}"],
        errors="coerce",
    )
    exit_frame[f"exit_adj_factor_{suffix}"] = pd.to_numeric(
        exit_frame[f"exit_adj_factor_{suffix}"],
        errors="coerce",
    )
    exit_frame[f"exit_adjusted_close_{suffix}"] = (
        exit_frame[f"exit_close_{suffix}"] * exit_frame[f"exit_adj_factor_{suffix}"]
    )

    output = frame.merge(exit_frame, on="symbol", how="left")
    mask_reason = _mask_reason(output, suffix=suffix)
    valid = mask_reason == "ok"

    returns = pd.Series(pd.NA, index=output.index, dtype="Float64")
    returns.loc[valid] = (
        output.loc[valid, f"exit_adjusted_close_{suffix}"]
        / output.loc[valid, "signal_adjusted_close"]
        - 1.0
    )

    benchmark = returns.loc[valid].mean()
    excess = pd.Series(pd.NA, index=output.index, dtype="Float64")
    rank = pd.Series(pd.NA, index=output.index, dtype="Float64")
    if pd.notna(benchmark):
        excess.loc[valid] = returns.loc[valid] - benchmark
        rank.loc[valid] = excess.loc[valid].rank(pct=True)

    output[f"future_return_{suffix}"] = returns
    output[f"future_benchmark_return_{suffix}"] = pd.Series(
        pd.NA,
        index=output.index,
        dtype="Float64",
    )
    if pd.notna(benchmark):
        output.loc[valid, f"future_benchmark_return_{suffix}"] = benchmark
    output[f"future_excess_return_{suffix}"] = excess
    output[f"y_rank_excess_return_{suffix}"] = rank
    output[f"label_valid_{suffix}"] = valid
    output[f"mask_reason_{suffix}"] = mask_reason
    return output


def _mask_reason(frame: pd.DataFrame, *, suffix: str) -> pd.Series:
    mask = pd.Series("ok", index=frame.index)
    mask.loc[frame["signal_close"].isna() | frame["signal_adj_factor"].isna()] = (
        "missing_signal_price"
    )
    mask.loc[
        (mask == "ok")
        & (
            frame[f"exit_close_{suffix}"].isna()
            | frame[f"exit_adj_factor_{suffix}"].isna()
        )
    ] = "missing_exit_price"
    mask.loc[
        (mask == "ok")
        & ((frame["signal_close"] <= 0) | (frame["signal_adj_factor"] <= 0))
    ] = "non_positive_signal_price"
    mask.loc[
        (mask == "ok")
        & (
            (frame[f"exit_close_{suffix}"] <= 0)
            | (frame[f"exit_adj_factor_{suffix}"] <= 0)
        )
    ] = "non_positive_exit_price"
    return mask


def _read_window(
    *,
    pm: PathManager,
    trade_dates: Sequence[str],
) -> pa.Table:
    window = tuple(trade_dates)
    access = Access(pm=pm, processed_version="v1")
    daily_tables = []
    adj_tables = []
    for date in window:
        daily_tables.append(
            pa.Table.from_pandas(
                access.daily_bars(trade_date=date),
                preserve_index=False,
            )
        )
        adjustment_path = pm.processed_data(
            dataset_name="adj_factor",
            version="v1",
            trade_date=date,
        )
        loaded_adjustment = meta.require(
            pm=pm,
            meta_path=pm.processed_meta(
                dataset_name="adj_factor",
                version="v1",
                trade_date=date,
            ),
            expected_payload_path=adjustment_path,
        )
        adj_tables.append(pq.ParquetFile(loaded_adjustment.payload_path).read())

    daily = pa.concat_tables(daily_tables)
    adj = pa.concat_tables(adj_tables)
    table_ops.require_columns(
        daily,
        ("symbol", "trade_date", "close"),
        who="daily_bar",
    )
    table_ops.require_columns(
        adj,
        ("symbol", "trade_date", "adj_factor"),
        who="adj_factor",
    )

    daily_df = daily.select(("symbol", "trade_date", "close")).to_pandas()
    adj_df = adj.select(("symbol", "trade_date", "adj_factor")).to_pandas()
    merged = daily_df.merge(adj_df, on=["symbol", "trade_date"], how="left")
    return pa.Table.from_pandas(merged, preserve_index=False)
