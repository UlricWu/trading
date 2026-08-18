# filepath: src/trading/market/daily_signal_data.py
from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pyarrow.parquet as pq

from src.access import Access, meta
from src.trading.market.daily_view import SYMBOL_COL
from src.utils import table_ops
from src.utils.path import PathManager

RAW_PRICE_COL = "close"


def read_daily_raw_signal_view_data(
    *,
    access: Access,
    pm: PathManager,
    symbols: Sequence[str],
    price_date: str,
    feature_date: str,
    feature_set: str,
    feature_version: str,
    feature_names: Sequence[str],
) -> pd.DataFrame:
    """Read one daily raw-price `DailyView` input for executable replay.

    Example:
        frame = read_daily_raw_signal_view_data(
            access=access,
            pm=path_manager,
            symbols=("000001",),
            price_date="2026-07-20",
            feature_date="2026-07-20",
            feature_set="daily",
            feature_version="v1",
            feature_names=("momentum",),
        )
    """
    ordered_symbols = [str(symbol) for symbol in symbols]
    names = [str(name) for name in feature_names]
    prices = read_raw_close(
        access=access,
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
    access: Access,
    trade_date: str,
    symbols: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Read `symbol, close` raw executable prices from daily_bar.

    Example:
        prices = read_raw_close(
            access=access,
            trade_date="2026-07-20",
            symbols=("000001",),
        )
    """
    if symbols is None:
        daily = access.daily_bars(trade_date=trade_date)
    else:
        ordered_symbols = [str(symbol) for symbol in symbols]
        daily = access.daily_bars(
            trade_date=trade_date,
            symbols=ordered_symbols,
        )

    table_ops.require_columns(daily, (SYMBOL_COL, RAW_PRICE_COL), who="daily_bar")
    prices = daily.loc[:, [SYMBOL_COL, RAW_PRICE_COL]].copy()
    table_ops.require_nonempty_strings(prices, (SYMBOL_COL,), who="daily_bar")
    table_ops.require_unique(prices, (SYMBOL_COL,), who="daily_bar")

    if symbols is None:
        table_ops.require_positive(prices, (RAW_PRICE_COL,), who="daily_bar")
        return prices.loc[:, [SYMBOL_COL, RAW_PRICE_COL]]

    prices = (
        prices.set_index(SYMBOL_COL, drop=False)
        .loc[[str(symbol) for symbol in symbols]]
        .reset_index(drop=True)
    )
    table_ops.require_positive(prices, (RAW_PRICE_COL,), who="daily_bar")
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

    table_ops.require_columns(features, (SYMBOL_COL, *names), who="feature")
    features = features.loc[:, [SYMBOL_COL, *names]].copy()
    table_ops.require_nonempty_strings(features, (SYMBOL_COL,), who="feature")
    table_ops.require_unique(features, (SYMBOL_COL,), who="feature")

    return (
        features.set_index(SYMBOL_COL, drop=False)
        .loc[ordered_symbols]
        .reset_index(drop=True)
    )
