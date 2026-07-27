# filepath: src/trading/market/daily_signal_data.py
from __future__ import annotations

import math
from collections.abc import Sequence

import pandas as pd
import pyarrow.parquet as pq

from src.access import Access, meta
from src.trading.market.daily_view import SYMBOL_COL
from src.utils.path import PathManager

RAW_PRICE_COL = "close"


def read_daily_raw_signal_view_data(
    *,
    pm: PathManager,
    symbols: Sequence[str],
    price_date: str,
    feature_date: str,
    feature_set: str,
    feature_version: str,
    feature_names: Sequence[str],
) -> pd.DataFrame:
    """Read one daily raw-price `DailyView` input for executable replay."""
    ordered_symbols = [str(symbol) for symbol in symbols]
    names = [str(name) for name in feature_names]
    prices = read_raw_close(
        pm=pm,
        trade_date=price_date,
        symbols=ordered_symbols,
    )
    features = _read_feature_rows(
        pm=pm,
        symbols=ordered_symbols,
        trade_date=feature_date,
        feature_set=feature_set,
        feature_version=feature_version,
        feature_names=names,
    )
    frame = features.copy()
    frame[RAW_PRICE_COL] = (
        prices.set_index(SYMBOL_COL).loc[ordered_symbols, RAW_PRICE_COL].to_numpy()
    )
    return frame.loc[:, [SYMBOL_COL, RAW_PRICE_COL, *names]]


def read_raw_close(
    *,
    pm: PathManager,
    trade_date: str,
    symbols: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Read `symbol, close` raw executable prices from daily_bar."""
    access = Access(pm=pm, processed_version="v1")
    if symbols is None:
        daily = access.daily_bars(trade_date=trade_date)
    else:
        ordered_symbols = [str(symbol) for symbol in symbols]
        daily = access.daily_bars(
            trade_date=trade_date,
            symbols=ordered_symbols,
        )

    _require_columns(daily, [SYMBOL_COL, RAW_PRICE_COL], "daily_bar")
    prices = daily.loc[:, [SYMBOL_COL, RAW_PRICE_COL]].copy()
    prices[SYMBOL_COL] = prices[SYMBOL_COL].astype(str)
    _require_unique_symbols(prices, "daily_bar")

    if symbols is None:
        _require_positive_finite(prices, [RAW_PRICE_COL])
        return prices.loc[:, [SYMBOL_COL, RAW_PRICE_COL]]

    prices = (
        prices.set_index(SYMBOL_COL, drop=False)
        .loc[[str(symbol) for symbol in symbols]]
        .reset_index(drop=True)
    )
    _require_positive_finite(prices, [RAW_PRICE_COL])
    return prices.loc[:, [SYMBOL_COL, RAW_PRICE_COL]]


def _read_feature_rows(
    *,
    pm: PathManager,
    symbols: Sequence[str],
    trade_date: str,
    feature_set: str,
    feature_version: str,
    feature_names: Sequence[str],
) -> pd.DataFrame:
    ordered_symbols = [str(symbol) for symbol in symbols]
    names = [str(name) for name in feature_names]
    path = pm.feature_data(
        feature_set=feature_set,
        version=feature_version,
        trade_date=trade_date,
    )
    loaded = meta.require(
        pm=pm,
        meta_path=pm.feature_meta(
            feature_set=feature_set,
            version=feature_version,
            trade_date=trade_date,
        ),
        expected_payload_path=path,
    )
    features = pq.ParquetFile(loaded.payload_path).read().to_pandas()

    _require_columns(features, [SYMBOL_COL, *names], "feature")
    features = features.loc[:, [SYMBOL_COL, *names]].copy()
    features[SYMBOL_COL] = features[SYMBOL_COL].astype(str)
    _require_unique_symbols(features, "feature")

    return (
        features.set_index(SYMBOL_COL, drop=False)
        .loc[ordered_symbols]
        .reset_index(drop=True)
    )


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"[daily_signal_data] {label} missing columns: {missing}")


def _require_positive_finite(
    frame: pd.DataFrame,
    columns: Sequence[str],
) -> None:
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        invalid_mask = ~values.map(math.isfinite) | (values <= 0.0)
        invalid = frame.loc[invalid_mask, SYMBOL_COL].tolist()
        if invalid:
            raise ValueError(
                f"[daily_signal_data] {column} must be finite and positive "
                f"for symbols: {invalid}"
            )
        frame[column] = values.astype(float)


def _require_unique_symbols(frame: pd.DataFrame, label: str) -> None:
    duplicated = (
        frame.loc[
            frame[SYMBOL_COL].duplicated(),
            SYMBOL_COL,
        ]
        .drop_duplicates()
        .tolist()
    )
    if duplicated:
        raise ValueError(
            f"[daily_signal_data] {label} rows must be one row per symbol; "
            f"duplicated symbols: {duplicated}"
        )
