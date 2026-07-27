# filepath: src/data_system/builders/label_daily_t1_net_excess_rank.py
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.access import Access, meta
from src.data_system.arrow.ops import require_columns
from src.data_system.builders.base import InputSpec
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


_REQUIRED_COLUMNS = (
    "symbol",
    "trade_date",
    "open",
    "close",
    "adj_factor",
)


@dataclass(frozen=True, slots=True)
class _OutputColumn:
    lookahead: int
    build: Callable[[pd.DataFrame], object]


_OUTPUT_COLUMNS: Mapping[str, _OutputColumn] = MappingProxyType(
    {
        "buy_date": _OutputColumn(1, lambda frame: frame["buy_date"]),
        "exit_date": _OutputColumn(2, lambda frame: frame["exit_date"]),
        "future_gross_return": _OutputColumn(
            2,
            lambda frame: _future_gross_return(frame),
        ),
        "future_net_return": _OutputColumn(
            2,
            lambda frame: _future_gross_return(frame),
        ),
        "future_net_excess_return": _OutputColumn(
            2,
            lambda frame: _future_net_excess_return(frame),
        ),
        "y_rank_net_excess_return": _OutputColumn(
            2,
            lambda frame: _rank_net_excess_return(frame),
        ),
        "label_valid": _OutputColumn(
            2,
            lambda frame: _mask_reason(frame) == "ok",
        ),
        "mask_reason": _OutputColumn(2, lambda frame: _mask_reason(frame)),
    }
)


class DailyT1NetExcessRankV1Builder:
    """Build v1 T+1 net excess-return rank labels."""

    key_columns = (
        "symbol",
        "trade_date",
    )
    output_columns = tuple(_OUTPUT_COLUMNS)

    @property
    def lookahead(self) -> int:
        return max(column.lookahead for column in _OUTPUT_COLUMNS.values())

    def target_lookahead(self, label_column: str) -> int:
        try:
            column = _OUTPUT_COLUMNS[label_column]
        except KeyError as exc:
            raise ValueError(f"unknown label_column: {label_column!r}") from exc
        return column.lookahead

    def read_input(self, *, pm: PathManager, trade_date: str) -> pa.Table:
        return _read_window(
            pm=pm,
            trade_date=trade_date,
            lookahead=self.lookahead,
        )

    def build_partition(
        self,
        table: pa.Table | Mapping[InputSpec, pa.Table],
    ) -> pa.Table:
        if not isinstance(table, pa.Table):
            raise TypeError("daily label input must be a pyarrow.Table")
        require_columns(table, _REQUIRED_COLUMNS)

        df = table.select(_REQUIRED_COLUMNS).to_pandas()
        dates = sorted(df["trade_date"].dropna().unique().tolist())
        if len(dates) < self.lookahead + 1:
            raise ValueError(
                "daily_t1_net_excess_rank requires 3 trade_date partitions"
            )

        signal_date, buy_date, exit_date = dates[:3]
        signal = df[df["trade_date"] == signal_date][["symbol", "trade_date"]].copy()
        buy = df[df["trade_date"] == buy_date][["symbol", "open", "adj_factor"]].rename(
            columns={
                "open": "buy_open",
                "adj_factor": "buy_adj_factor",
            }
        )
        exit_ = df[df["trade_date"] == exit_date][
            ["symbol", "close", "adj_factor"]
        ].rename(
            columns={
                "close": "exit_close",
                "adj_factor": "exit_adj_factor",
            }
        )

        frame = signal.merge(buy, on="symbol", how="left")
        frame = frame.merge(exit_, on="symbol", how="left")
        for column_name in (
            "buy_open",
            "buy_adj_factor",
            "exit_close",
            "exit_adj_factor",
        ):
            frame[column_name] = pd.to_numeric(frame[column_name], errors="coerce")
        frame["buy_date"] = buy_date
        frame["exit_date"] = exit_date

        output = frame[list(self.key_columns)].copy()
        for column_name, column in _OUTPUT_COLUMNS.items():
            output[column_name] = column.build(frame)

        result = output[[*self.key_columns, *self.output_columns]].sort_values(
            list(self.key_columns)
        )
        return pa.Table.from_pandas(result, preserve_index=False)


def _mask_reason(frame: pd.DataFrame) -> pd.Series:
    mask = pd.Series("ok", index=frame.index)
    mask.loc[frame["buy_open"].isna() | frame["buy_adj_factor"].isna()] = (
        "missing_buy_bar"
    )
    mask.loc[
        (mask == "ok") & (frame["exit_close"].isna() | frame["exit_adj_factor"].isna())
    ] = "missing_exit_bar"
    mask.loc[
        (mask == "ok") & ((frame["buy_open"] <= 0) | (frame["buy_adj_factor"] <= 0))
    ] = "non_positive_buy_price"
    mask.loc[
        (mask == "ok") & ((frame["exit_close"] <= 0) | (frame["exit_adj_factor"] <= 0))
    ] = "non_positive_exit_price"
    return mask


def _label_valid(frame: pd.DataFrame) -> pd.Series:
    return _mask_reason(frame) == "ok"


def _future_gross_return(frame: pd.DataFrame) -> pd.Series:
    valid = _label_valid(frame)
    output = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    adjusted_buy_open = frame.loc[valid, "buy_open"].astype(float) * frame.loc[
        valid, "buy_adj_factor"
    ].astype(float)
    adjusted_exit_close = frame.loc[valid, "exit_close"].astype(float) * frame.loc[
        valid, "exit_adj_factor"
    ].astype(float)
    output.loc[valid] = adjusted_exit_close / adjusted_buy_open - 1.0
    return output


def _future_net_excess_return(frame: pd.DataFrame) -> pd.Series:
    returns = _future_gross_return(frame)
    valid = _label_valid(frame)
    median_return = returns.loc[valid].median()
    output = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    output.loc[valid] = returns.loc[valid] - median_return
    return output


def _rank_net_excess_return(frame: pd.DataFrame) -> pd.Series:
    excess = _future_net_excess_return(frame)
    valid = _label_valid(frame)
    output = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    output.loc[valid] = excess.loc[valid].rank(pct=True)
    return output


def _read_window(
    *,
    pm: PathManager,
    trade_date: str,
    lookahead: int,
) -> pa.Table:
    window = _window_dates(pm=pm, trade_date=trade_date, lookahead=lookahead)
    access = Access(pm=pm, processed_version="v1")
    daily_tables = [
        pa.Table.from_pandas(
            access.daily_bars(trade_date=date),
            preserve_index=False,
        )
        for date in window
    ]
    adj_tables = []
    for date in window[1:]:
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
    require_columns(daily, ("symbol", "trade_date", "open", "close"))
    require_columns(adj, ("symbol", "trade_date", "adj_factor"))

    daily_df = daily.select(("symbol", "trade_date", "open", "close")).to_pandas()
    adj_df = adj.select(("symbol", "trade_date", "adj_factor")).to_pandas()
    merged = daily_df.merge(adj_df, on=["symbol", "trade_date"], how="left")
    return pa.Table.from_pandas(merged, preserve_index=False)


def _window_dates(
    *,
    pm: PathManager,
    trade_date: str,
    lookahead: int,
) -> list[str]:
    dataset_name = "daily_bar"
    version = "v1"
    trade_date = DateTimeUtils.require_system_date(
        trade_date,
        field_name="trade_date",
    )
    root = pm.processed_version_dir(
        dataset_name=dataset_name,
        version=version,
    )
    dates = []
    for path in root.glob("trade_date=*"):
        if not path.is_dir():
            continue
        dates.append(
            DateTimeUtils.require_system_date(
                path.name.removeprefix("trade_date="),
                field_name="trade_date",
            )
        )

    dates = sorted(set(dates))
    try:
        index = dates.index(trade_date)
    except ValueError as exc:
        raise RuntimeError(
            f"[LabelBuild] missing signal partition trade_date={trade_date}"
        ) from exc

    window = dates[index : index + lookahead + 1]
    if len(window) != lookahead + 1:
        raise RuntimeError(
            f"[LabelBuild] insufficient daily_t1 window trade_date={trade_date}"
        )
    return window
