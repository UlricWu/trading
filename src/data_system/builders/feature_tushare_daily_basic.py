# filepath: src/data_system/builders/feature_tushare_daily_basic.py
"""Tushare daily bar feature builder."""

from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.access import Access, meta
from src.utils import table_ops
from src.utils.path import PathManager
from src.utils.price_utils import apply_asof_price_adjustment

_REQUIRED_COLUMNS = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
    "adj_factor",
    "turnover_rate",
)

_DAILY_BAR_INPUT_COLUMNS = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vol",
    "amount",
)
_ADJ_FACTOR_INPUT_COLUMNS = ("symbol", "trade_date", "adj_factor")
_DAILY_BASIC_INPUT_COLUMNS = ("symbol", "trade_date", "turnover_rate")

_FEATURE_LOOKBACKS = MappingProxyType(
    {
        "f_d_return": 1,
        "f_d_gap": 1,
        "f_d_intraday_return": 0,
        "f_d_range": 1,
        "f_d_log_volume": 0,
        "f_d_log_amount": 0,
        "f_d_max_drawdown_20d_asof_tminus1": 20,
        "f_d_volatility_60d_asof_tminus1": 61,
        "f_d_distance_to_20d_high_asof_tminus1": 20,
        "f_d_amount_mean_5d_asof_tminus1": 5,
        "f_d_amount_mean_20d_asof_tminus1": 20,
        "f_d_ret_5d_asof_tminus1": 5,
        "f_d_ret_20d_asof_tminus1": 20,
        "f_d_volatility_20d_asof_tminus1": 21,
        "f_d_turnover_mean_20d_asof_tminus1": 20,
        "f_d_position_in_20d_range_asof_tminus1": 20,
    }
)
_OUTPUT_COLUMNS = tuple(_FEATURE_LOOKBACKS)


class TushareDailyBasicV1Builder:
    """Build the v1 daily Tushare feature partition.

    Example:
        builder = TushareDailyBasicV1Builder()
        features = builder.build_partition(table)
    """

    key_columns: tuple[str, ...] = (
        "symbol",
        "trade_date",
    )
    output_columns: tuple[str, ...] = tuple(_OUTPUT_COLUMNS)

    @property
    def lookback(self) -> int:
        return max(_FEATURE_LOOKBACKS.values())

    def read_input(
        self,
        *,
        access: Access,
        pm: PathManager,
        processed_version: str,
        trade_date: str,
    ) -> pa.Table:
        """Read the formal history needed for one feature partition.

        Example:
            table = TushareDailyBasicV1Builder().read_input(
                access=access,
                pm=path_manager,
                processed_version="v1",
                trade_date="2026-07-20",
            )
        """
        return _read_history(
            access=access,
            pm=pm,
            processed_version=processed_version,
            trade_date=trade_date,
            lookback=self.lookback,
            daily_basic_lookback=20,
        )

    def build_partition(self, table: pa.Table) -> pa.Table:
        """Return the daily feature partition without an intraday phase.

        Example:
            features = TushareDailyBasicV1Builder().build_partition(table)
        """
        return _build_partition(
            table=table,
            required_columns=_REQUIRED_COLUMNS,
            key_columns=self.key_columns,
        )


def _build_partition(
    *,
    table: pa.Table,
    required_columns: tuple[str, ...],
    key_columns: tuple[str, ...],
) -> pa.Table:
    table_ops.require_columns(
        table,
        required_columns,
        who="TushareDailyBasicV1Builder input",
    )
    table_ops.require_nonempty(table, who="TushareDailyBasicV1Builder input")

    df = table.select(required_columns).to_pandas()
    df = df.sort_values(list(key_columns)).reset_index(drop=True)
    output_date = df["trade_date"].max()
    df = apply_asof_price_adjustment(
        df,
        adjustment="qfq",
        asof_date=output_date,
        price_columns=("open", "close", "high", "low"),
        output_prefix="qfq_",
    )
    current = df.loc[df["trade_date"] == output_date].copy()
    history = df.loc[df["trade_date"] < output_date].copy()
    features = current.loc[:, list(key_columns)].copy()
    symbols = current["symbol"]

    previous_close = _latest_positive_by_symbol(history, "qfq_close")
    current_qfq_close = _numeric(current["qfq_close"])
    current_qfq_open = _numeric(current["qfq_open"])
    current_qfq_high = _numeric(current["qfq_high"])
    current_qfq_low = _numeric(current["qfq_low"])
    mapped_previous_close = symbols.map(previous_close)

    features["f_d_return"] = _positive_ratio_minus_one(
        current_qfq_close,
        mapped_previous_close,
    )
    features["f_d_gap"] = _positive_ratio_minus_one(
        current_qfq_open,
        mapped_previous_close,
    )
    features["f_d_intraday_return"] = _ratio_minus_one_series(
        _numeric(current["close"]),
        _numeric(current["open"]),
    )
    features["f_d_range"] = (
        (current_qfq_high - current_qfq_low) / mapped_previous_close
    ).where(
        (current_qfq_high > 0) & (current_qfq_low > 0) & (mapped_previous_close > 0)
    )
    volume = _numeric(current["vol"])
    amount = _numeric(current["amount"])
    features["f_d_log_volume"] = np.log1p(volume.where(volume >= 0))
    features["f_d_log_amount"] = np.log1p(amount.where(amount >= 0))

    metrics = {
        "f_d_max_drawdown_20d_asof_tminus1": _max_drawdown_by_symbol(history, 20),
        "f_d_volatility_60d_asof_tminus1": _volatility_by_symbol(history, 60),
        "f_d_distance_to_20d_high_asof_tminus1": _distance_to_high_by_symbol(
            history, 20
        ),
        "f_d_amount_mean_5d_asof_tminus1": _mean_positive_by_symbol(
            history, "amount", 5
        ),
        "f_d_amount_mean_20d_asof_tminus1": _mean_positive_by_symbol(
            history, "amount", 20
        ),
        "f_d_ret_5d_asof_tminus1": _return_by_symbol(history, 5),
        "f_d_ret_20d_asof_tminus1": _return_by_symbol(history, 20),
        "f_d_volatility_20d_asof_tminus1": _volatility_by_symbol(history, 20),
        "f_d_turnover_mean_20d_asof_tminus1": _mean_positive_by_symbol(
            history, "turnover_rate", 20
        ),
        "f_d_position_in_20d_range_asof_tminus1": _position_in_range_by_symbol(
            history, 20
        ),
    }
    for column_name, metric_by_symbol in metrics.items():
        features[column_name] = symbols.map(metric_by_symbol)

    return pa.Table.from_pandas(
        features.loc[:, [*key_columns, *_OUTPUT_COLUMNS]],
        preserve_index=False,
    )


def _numeric(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce")


def _positive_ratio_minus_one(
    numerator: pd.Series,
    denominator: pd.Series,
) -> pd.Series:
    return (numerator / denominator - 1.0).where((numerator > 0) & (denominator > 0))


def _ratio_minus_one_series(
    numerator: pd.Series,
    denominator: pd.Series,
) -> pd.Series:
    return (numerator / denominator - 1.0).where(denominator > 0)


def _positive_window(
    history: pd.DataFrame,
    columns: tuple[str, ...],
    window: int,
) -> tuple[pd.DataFrame, dict[str, pd.Series], pd.Series]:
    window_frame = history.groupby("symbol", sort=False).tail(window).copy()
    values = {column: _numeric(window_frame[column]) for column in columns}
    valid_rows = pd.Series(True, index=window_frame.index)
    for column_values in values.values():
        valid_rows &= column_values.notna() & (column_values > 0)
    valid_by_symbol = valid_rows.groupby(window_frame["symbol"]).all()
    valid_by_symbol &= window_frame.groupby("symbol").size().eq(window)
    return window_frame, values, valid_by_symbol


def _latest_positive_by_symbol(
    history: pd.DataFrame,
    column: str,
) -> pd.Series:
    window_frame, values, valid = _positive_window(history, (column,), 1)
    latest = pd.Series(values[column].to_numpy(), index=window_frame["symbol"])
    return latest.where(valid)


def _max_drawdown_by_symbol(history: pd.DataFrame, window: int) -> pd.Series:
    frame, values, valid = _positive_window(history, ("qfq_close",), window)
    symbols = frame["symbol"]
    close = values["qfq_close"]
    drawdown = close / close.groupby(symbols).cummax() - 1.0
    return drawdown.groupby(symbols).min().where(valid)


def _volatility_by_symbol(history: pd.DataFrame, window: int) -> pd.Series:
    frame, values, valid = _positive_window(history, ("qfq_close",), window + 1)
    symbols = frame["symbol"]
    returns = values["qfq_close"].groupby(symbols).pct_change(fill_method=None)
    return returns.groupby(symbols).std().where(valid)


def _distance_to_high_by_symbol(
    history: pd.DataFrame,
    window: int,
) -> pd.Series:
    frame, values, valid = _positive_window(history, ("qfq_high",), window)
    high_max = values["qfq_high"].groupby(frame["symbol"]).max()
    latest_close = _latest_positive_by_symbol(history, "qfq_close")
    return (latest_close / high_max - 1.0).where(valid & latest_close.notna())


def _mean_positive_by_symbol(
    history: pd.DataFrame,
    column: str,
    window: int,
) -> pd.Series:
    frame, values, valid = _positive_window(history, (column,), window)
    return values[column].groupby(frame["symbol"]).mean().where(valid)


def _return_by_symbol(history: pd.DataFrame, window: int) -> pd.Series:
    frame, values, valid = _positive_window(history, ("qfq_close",), window)
    close = values["qfq_close"]
    symbols = frame["symbol"]
    first = close.groupby(symbols).first()
    last = close.groupby(symbols).last()
    return (last / first - 1.0).where(valid)


def _position_in_range_by_symbol(
    history: pd.DataFrame,
    window: int,
) -> pd.Series:
    frame, values, valid = _positive_window(
        history,
        ("qfq_high", "qfq_low"),
        window,
    )
    symbols = frame["symbol"]
    high_max = values["qfq_high"].groupby(symbols).max()
    low_min = values["qfq_low"].groupby(symbols).min()
    latest_close = _latest_positive_by_symbol(history, "qfq_close")
    width = high_max - low_min
    return ((latest_close - low_min) / width).where(
        valid & latest_close.notna() & (width > 0)
    )


def _read_history(
    *,
    access: Access,
    pm: PathManager,
    processed_version: str,
    trade_date: str,
    lookback: int,
    daily_basic_lookback: int | None = None,
) -> pa.Table:
    dates = access.recent_trade_dates(
        end_date=trade_date,
        sessions=lookback + 1,
    )

    daily_tables = []
    adj_tables = []
    for date in dates:
        daily_tables.append(
            _read_processed_columns(
                pm=pm,
                dataset_name="daily_bar",
                version=processed_version,
                trade_date=date,
                columns=_DAILY_BAR_INPUT_COLUMNS,
            )
        )
        adj_tables.append(
            _read_processed_columns(
                pm=pm,
                dataset_name="adj_factor",
                version=processed_version,
                trade_date=date,
                columns=_ADJ_FACTOR_INPUT_COLUMNS,
            )
        )

    daily = pa.concat_tables(daily_tables)
    adj = pa.concat_tables(adj_tables)

    daily_df = daily.to_pandas()
    adj_df = adj.to_pandas()
    merged = daily_df.merge(adj_df, on=["symbol", "trade_date"], how="left")
    if daily_basic_lookback is not None:
        basic_dates = dates[:-1][-daily_basic_lookback:]
        basic_tables = [
            _read_processed_columns(
                pm=pm,
                dataset_name="daily_basic",
                version=processed_version,
                trade_date=date,
                columns=_DAILY_BASIC_INPUT_COLUMNS,
            )
            for date in basic_dates
        ]
        if basic_tables:
            daily_basic = pa.concat_tables(basic_tables)
            basic_df = daily_basic.to_pandas()
        else:
            basic_df = pd.DataFrame(columns=["symbol", "trade_date", "turnover_rate"])
        merged = merged.merge(basic_df, on=["symbol", "trade_date"], how="left")
    return pa.Table.from_pandas(merged, preserve_index=False)


def _read_processed_columns(
    *,
    pm: PathManager,
    dataset_name: str,
    version: str,
    trade_date: str,
    columns: tuple[str, ...],
) -> pa.Table:
    path = pm.processed_data(
        dataset_name=dataset_name,
        version=version,
        trade_date=trade_date,
    )
    loaded = meta.require(
        pm=pm,
        meta_path=pm.processed_meta(
            dataset_name=dataset_name,
            version=version,
            trade_date=trade_date,
        ),
        expected_payload_path=path,
    )
    table = pq.ParquetFile(loaded.payload_path).read()
    table_ops.require_columns(table, columns, who=dataset_name)
    return table.select(columns)
