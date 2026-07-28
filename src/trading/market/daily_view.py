# filepath: src/trading/market/daily_view.py
from __future__ import annotations

from datetime import date, time
from typing import Sequence

import numpy as np
import pandas as pd

from src.trading.market.data_view import MarketDataView
from src.utils.datetime_utils import DateTimeUtils


SYMBOL_COL = "symbol"
PRICE_COL = "adjusted_close"


class DailyView(MarketDataView):
    """Expose one observable daily bar without inventing an intraday phase.

    Example:
        view = DailyView(
            pd.DataFrame({"symbol": ["600000"], "adjusted_close": [10.0]}),
            trade_date="2026-07-27",
        )
        view.on_time(view.bar_timestamps_us()[0])
        price = view.get_price("600000")
    """

    def __init__(
        self,
        data: pd.DataFrame,
        *,
        trade_date: str,
        price_column: str = PRICE_COL,
        ts_us: int | None = None,
    ) -> None:
        if data.empty:
            raise ValueError("DailyView requires non-empty data")

        price_column = _validated_price_column(price_column)
        frame = data.reset_index(drop=True)
        frame[SYMBOL_COL] = _validated_symbols(frame[SYMBOL_COL])

        self._trade_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )
        self._ts_us = (
            int(ts_us) if ts_us is not None else _default_ts_us(self._trade_date)
        )
        self._symbols = frame[SYMBOL_COL].tolist()
        self._sym2idx = {str(symbol): idx for idx, symbol in enumerate(self._symbols)}
        self._price_column = price_column
        self._feature_names = tuple(
            str(column)
            for column in frame.columns
            if column not in {SYMBOL_COL, self._price_column}
        )
        self._price_vec = frame[self._price_column].to_numpy(
            dtype=np.float64,
            na_value=np.nan,
        )
        self._feature_mat = _float_matrix(frame, self._feature_names)
        self._current_ts: int | None = None

    def on_time(self, ts_us: int) -> None:
        ts_us = int(ts_us)
        if ts_us < self._ts_us:
            raise RuntimeError(
                f"DailyView facts are not observable before ts_us={self._ts_us}"
            )
        self._current_ts = ts_us

    def time_bounds_us(self) -> tuple[int, int]:
        return self._ts_us, self._ts_us

    def bar_timestamps_us(self) -> list[int]:
        return [self._ts_us]

    def get_feature_matrix(self, symbols: Sequence[str]) -> np.ndarray:
        self._require_active()
        return self._feature_mat[self._indices_for_symbols(symbols), :]

    def get_price_vector(self, symbols: Sequence[str]) -> np.ndarray:
        self._require_active()
        return self._price_vec[self._indices_for_symbols(symbols)]

    def get_price(self, symbol: str) -> float | None:
        self._require_active()
        value = self._price_vec[self._index_for_symbol(symbol)]
        if not np.isfinite(value):
            return None
        return float(value)

    @property
    def frequency(self) -> str:
        return "daily"

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def trade_date(self) -> str:
        return self._trade_date

    @property
    def feature_names(self) -> list[str]:
        return list(self._feature_names)

    @property
    def price_column(self) -> str:
        return self._price_column

    def _require_active(self) -> None:
        if self._current_ts is None:
            raise RuntimeError("DailyView.on_time(ts_us) must be called before query")

    def _index_for_symbol(self, symbol: str) -> int:
        symbol = str(symbol)
        try:
            return self._sym2idx[symbol]
        except KeyError as exc:
            raise KeyError(f"symbol not found: {symbol}") from exc

    def _indices_for_symbols(self, symbols: Sequence[str]) -> np.ndarray:
        normalized = [str(symbol) for symbol in symbols]
        missing = sorted(
            {symbol for symbol in normalized if symbol not in self._sym2idx}
        )
        if missing:
            raise KeyError(f"symbols not found: {missing}")
        return np.fromiter(
            (self._sym2idx[symbol] for symbol in normalized),
            dtype=np.int64,
            count=len(normalized),
        )


def _validated_symbols(series: pd.Series) -> pd.Series:
    if series.isna().any():
        raise ValueError("DailyView symbol column contains null values")

    symbols = series.astype(str)
    if (symbols.str.len() == 0).any():
        raise ValueError("DailyView symbol column contains empty values")

    duplicated = symbols[symbols.duplicated()].drop_duplicates().tolist()
    if duplicated:
        raise ValueError(f"DailyView duplicate symbols: {duplicated}")
    return symbols


def _validated_price_column(price_column: str) -> str:
    price_column = str(price_column)
    if not price_column:
        raise ValueError("DailyView price_column is required")
    if price_column == SYMBOL_COL:
        raise ValueError("DailyView price_column must not be symbol")
    return price_column


def _default_ts_us(trade_date: str) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(
        time(15, 0),
        date.fromisoformat(trade_date),
    )


def _float_matrix(frame: pd.DataFrame, columns: Sequence[str]) -> np.ndarray:
    if not columns:
        return np.empty((len(frame), 0), dtype=np.float64)
    return frame.loc[:, list(columns)].to_numpy(dtype=np.float64, na_value=np.nan)
