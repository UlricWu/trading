# filepath: src/data_system/builders/feature_tushare_daily_basic.py
"""Post-close daily features built from formal Tushare objects."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow as pa

from src.access import Access
from src.utils import table_ops
from src.utils.price_utils import apply_asof_price_adjustment

_KEY_COLUMNS = ("symbol", "trade_date")
_DAILY_BAR_COLUMNS = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
)
_OUTPUT_COLUMNS = (
    "f_d_close_return_1d",
    "f_d_open_gap_1d",
    "f_d_intraday_return",
    "f_d_range_vs_prev_close",
    "f_d_log_volume",
    "f_d_log_amount",
    "f_d_max_drawdown_20d_asof_tminus1",
    "f_d_close_volatility_60d_asof_tminus1",
    "f_d_close_distance_to_high_20d_asof_tminus1",
    "f_d_amount_mean_5d_asof_tminus1",
    "f_d_amount_mean_20d_asof_tminus1",
    "f_d_close_return_5d_asof_tminus1",
    "f_d_close_return_20d_asof_tminus1",
    "f_d_close_volatility_20d_asof_tminus1",
    "f_d_turnover_rate_mean_20d_asof_tminus1",
    "f_d_close_position_in_range_20d_asof_tminus1",
)
_LONG_VOLATILITY_WINDOW = 60
_HISTORY_SESSIONS = _LONG_VOLATILITY_WINDOW + 1
_TURNOVER_WINDOW = 20


class TushareDailyBasicV1Builder:
    """Build one post-close ``tushare_daily_basic/v1`` partition.

    Example:
        features = TushareDailyBasicV1Builder().build(
            access=access,
            trade_date="2026-07-20",
        )
    """

    def build(
        self,
        *,
        access: Access,
        trade_date: str,
    ) -> pa.Table:
        """Return features whose identity and as-of date are ``trade_date``.

        Example:
            features = TushareDailyBasicV1Builder().build(
                access=access,
                trade_date="2026-07-20",
            )
        """
        dates = tuple(
            access.recent_trade_dates(
                end_date=trade_date,
                sessions=_HISTORY_SESSIONS + 1,
            )
        )
        output_date = dates[-1]
        daily_parts: list[pd.DataFrame] = []
        current_symbols: tuple[str, ...] = ()
        for date in dates:
            bars = access.daily_bars(trade_date=date)
            table_ops.require_columns(
                bars,
                _DAILY_BAR_COLUMNS,
                who="tushare_daily_basic daily_bar",
            )
            if date == output_date:
                current_symbols = tuple(bars["symbol"])
            factors = access.adjustment_factors(trade_date=date)
            daily_parts.append(
                bars.loc[:, list(_DAILY_BAR_COLUMNS)].merge(
                    factors,
                    on=list(_KEY_COLUMNS),
                    how="left",
                    validate="one_to_one",
                )
            )

        frame = pd.concat(daily_parts, ignore_index=True)
        frame = frame.loc[frame["symbol"].isin(current_symbols)].copy()
        expected_keys = pd.MultiIndex.from_product(
            (current_symbols, dates),
            names=_KEY_COLUMNS,
        ).to_frame(index=False)
        frame = expected_keys.merge(
            frame,
            on=list(_KEY_COLUMNS),
            how="left",
            validate="one_to_one",
        )
        turnover_dates = dates[-(_TURNOVER_WINDOW + 1) : -1]
        turnover = pd.concat(
            [access.turnover_rates(trade_date=date) for date in turnover_dates],
            ignore_index=True,
        )
        frame = frame.merge(
            turnover,
            on=list(_KEY_COLUMNS),
            how="left",
            validate="one_to_one",
        )
        frame = frame.sort_values(list(_KEY_COLUMNS)).reset_index(drop=True)
        frame = apply_asof_price_adjustment(
            frame,
            adjustment="qfq",
            asof_date=output_date,
            price_columns=("open", "close", "high", "low"),
            output_prefix="qfq_",
        )

        current = frame.loc[frame["trade_date"] == output_date].copy()
        history = frame.loc[frame["trade_date"] < output_date].copy()
        features = current.loc[:, list(_KEY_COLUMNS)].copy()
        symbols = current["symbol"]

        previous_close = _latest_by_symbol(history, "qfq_close")
        current_close = current["qfq_close"]
        current_open = current["qfq_open"]
        current_high = current["qfq_high"]
        current_low = current["qfq_low"]
        mapped_previous_close = symbols.map(previous_close)

        features["f_d_close_return_1d"] = _ratio_minus_one(
            current_close,
            mapped_previous_close,
        )
        features["f_d_open_gap_1d"] = _ratio_minus_one(
            current_open,
            mapped_previous_close,
        )
        features["f_d_intraday_return"] = _ratio_minus_one(
            _positive(current["close"]),
            _positive(current["open"]),
        )
        features["f_d_range_vs_prev_close"] = (
            (current_high - current_low) / mapped_previous_close
        ).where(mapped_previous_close.notna())
        features["f_d_log_volume"] = np.log1p(_non_negative(current["vol"]))
        features["f_d_log_amount"] = np.log1p(_non_negative(current["amount"]))

        metrics = {
            "f_d_max_drawdown_20d_asof_tminus1": _max_drawdown_by_symbol(history, 20),
            "f_d_close_volatility_60d_asof_tminus1": _volatility_by_symbol(
                history,
                _LONG_VOLATILITY_WINDOW,
            ),
            "f_d_close_distance_to_high_20d_asof_tminus1": (
                _distance_to_high_by_symbol(history, 20)
            ),
            "f_d_amount_mean_5d_asof_tminus1": _mean_non_negative_by_symbol(
                history, "amount", 5
            ),
            "f_d_amount_mean_20d_asof_tminus1": _mean_non_negative_by_symbol(
                history, "amount", 20
            ),
            "f_d_close_return_5d_asof_tminus1": _return_by_symbol(history, 5),
            "f_d_close_return_20d_asof_tminus1": _return_by_symbol(history, 20),
            "f_d_close_volatility_20d_asof_tminus1": _volatility_by_symbol(history, 20),
            "f_d_turnover_rate_mean_20d_asof_tminus1": (
                _mean_non_negative_by_symbol(
                    history,
                    "turnover_rate",
                    _TURNOVER_WINDOW,
                )
            ),
            "f_d_close_position_in_range_20d_asof_tminus1": (
                _position_in_range_by_symbol(history, 20)
            ),
        }
        for column_name, metric_by_symbol in metrics.items():
            features[column_name] = symbols.map(metric_by_symbol)
        for column_name in _OUTPUT_COLUMNS:
            features[column_name] = pd.to_numeric(
                features[column_name],
                errors="coerce",
            ).astype("Float64")

        return pa.Table.from_pandas(
            features.loc[:, [*_KEY_COLUMNS, *_OUTPUT_COLUMNS]],
            preserve_index=False,
        )


def _finite_numeric(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return numeric.where(np.isfinite(numeric))


def _positive(values: pd.Series) -> pd.Series:
    numeric = _finite_numeric(values)
    return numeric.where(numeric > 0)


def _non_negative(values: pd.Series) -> pd.Series:
    numeric = _finite_numeric(values)
    return numeric.where(numeric >= 0)


def _ratio_minus_one(
    numerator: pd.Series,
    denominator: pd.Series,
) -> pd.Series:
    return numerator / denominator - 1.0


def _complete_window(
    history: pd.DataFrame,
    columns: tuple[str, ...],
    window: int,
) -> tuple[pd.DataFrame, dict[str, pd.Series], pd.Series]:
    frame = history.groupby("symbol", sort=False).tail(window).copy()
    values = {column: frame[column] for column in columns}
    valid_rows = pd.Series(True, index=frame.index)
    for column_values in values.values():
        valid_rows &= column_values.notna()
    valid = valid_rows.groupby(frame["symbol"]).all()
    valid &= frame.groupby("symbol").size().eq(window)
    return frame, values, valid


def _latest_by_symbol(
    history: pd.DataFrame,
    column: str,
) -> pd.Series:
    frame, values, valid = _complete_window(history, (column,), 1)
    latest = values[column].groupby(frame["symbol"]).last()
    return latest.where(valid)


def _max_drawdown_by_symbol(history: pd.DataFrame, window: int) -> pd.Series:
    frame, values, valid = _complete_window(history, ("qfq_close",), window)
    symbols = frame["symbol"]
    close = values["qfq_close"]
    drawdown = close / close.groupby(symbols).cummax() - 1.0
    return drawdown.groupby(symbols).min().where(valid)


def _volatility_by_symbol(history: pd.DataFrame, window: int) -> pd.Series:
    frame, values, valid = _complete_window(history, ("qfq_close",), window + 1)
    symbols = frame["symbol"]
    returns = values["qfq_close"].groupby(symbols).pct_change(fill_method=None)
    return returns.groupby(symbols).std(ddof=1).where(valid)


def _distance_to_high_by_symbol(
    history: pd.DataFrame,
    window: int,
) -> pd.Series:
    frame, values, valid = _complete_window(history, ("qfq_high",), window)
    high_max = values["qfq_high"].groupby(frame["symbol"]).max()
    latest_close = _latest_by_symbol(history, "qfq_close")
    return (latest_close / high_max - 1.0).where(valid & latest_close.notna())


def _mean_non_negative_by_symbol(
    history: pd.DataFrame,
    column: str,
    window: int,
) -> pd.Series:
    frame = history.groupby("symbol", sort=False).tail(window).copy()
    values = _non_negative(frame[column])
    valid = values.notna().groupby(frame["symbol"]).all()
    valid &= frame.groupby("symbol").size().eq(window)
    return values.groupby(frame["symbol"]).mean().where(valid)


def _return_by_symbol(history: pd.DataFrame, window: int) -> pd.Series:
    frame, values, valid = _complete_window(history, ("qfq_close",), window + 1)
    close = values["qfq_close"]
    symbols = frame["symbol"]
    first = close.groupby(symbols).first()
    last = close.groupby(symbols).last()
    return (last / first - 1.0).where(valid)


def _position_in_range_by_symbol(
    history: pd.DataFrame,
    window: int,
) -> pd.Series:
    frame, values, valid = _complete_window(
        history,
        ("qfq_high", "qfq_low"),
        window,
    )
    symbols = frame["symbol"]
    high_max = values["qfq_high"].groupby(symbols).max()
    low_min = values["qfq_low"].groupby(symbols).min()
    latest_close = _latest_by_symbol(history, "qfq_close")
    width = high_max - low_min
    return ((latest_close - low_min) / width).where(
        valid & latest_close.notna() & (width > 0)
    )
