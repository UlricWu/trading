# filepath: tests/data_system/builders/test_feature_tushare_daily_basic.py
"""Behavior tests for post-close Tushare daily features."""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from src.access import Access
from src.data_system.builders.feature_tushare_daily_basic import (
    TushareDailyBasicV1Builder,
)

_FEATURE_COLUMNS = (
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


class _Access:
    def __init__(
        self,
        *,
        missing_close_index: int | None = None,
        missing_row_index: int | None = None,
    ) -> None:
        self.dates = tuple(
            pd.bdate_range(end="2026-07-20", periods=62).strftime("%Y-%m-%d")
        )
        self._index_by_date = {date: index for index, date in enumerate(self.dates)}
        self._missing_close_index = missing_close_index
        self._missing_row_index = missing_row_index
        self.turnover_dates: list[str] = []

    def daily_bars(self, *, trade_date: str) -> pd.DataFrame:
        index = self._index_by_date[trade_date]
        symbols = ["000001", "000002"]
        if trade_date != self.dates[-1]:
            symbols.append("999999")
        rows = []
        for symbol in symbols:
            if symbol == "000001" and index == self._missing_row_index:
                continue
            scale = {"000001": 1.0, "000002": 2.0, "999999": 3.0}[symbol]
            close = scale * (100.0 + index)
            if symbol == "000001" and index == self._missing_close_index:
                close = np.nan
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "open": scale * (99.0 + index),
                    "high": scale * (101.0 + index),
                    "low": scale * (98.0 + index),
                    "close": close,
                    "vol": 0.0 if symbol == "000001" else 100.0,
                    "amount": 1000.0,
                }
            )
        return pd.DataFrame(rows)

    def adjustment_factors(self, *, trade_date: str) -> pd.DataFrame:
        bars = self.daily_bars(trade_date=trade_date)
        return bars.loc[:, ["symbol", "trade_date"]].assign(adj_factor=1.0)

    def turnover_rates(self, *, trade_date: str) -> pd.DataFrame:
        self.turnover_dates.append(trade_date)
        return pd.DataFrame(
            {
                "symbol": ["000001", "000002"],
                "trade_date": [trade_date, trade_date],
                "turnover_rate": [0.0, 2.0],
            }
        )


def test_feature_builder_exposes_exact_post_close_schema_and_windows() -> None:
    access = _Access()

    result = TushareDailyBasicV1Builder().build(
        access=cast(Access, access),
        trade_dates=access.dates,
    )
    frame = result.to_pandas()

    assert result.column_names == ["symbol", "trade_date", *_FEATURE_COLUMNS]
    assert frame["symbol"].tolist() == ["000001", "000002"]
    assert frame["trade_date"].tolist() == [access.dates[-1]] * 2
    assert frame.loc[0, "f_d_close_return_1d"] == pytest.approx(161.0 / 160.0 - 1)
    assert frame.loc[0, "f_d_close_return_5d_asof_tminus1"] == pytest.approx(
        160.0 / 155.0 - 1
    )
    assert frame.loc[0, "f_d_turnover_rate_mean_20d_asof_tminus1"] == 0.0
    assert pd.notna(frame.loc[0, "f_d_close_volatility_60d_asof_tminus1"])
    assert access.turnover_dates == list(access.dates[-21:-1])
    assert all(
        result.schema.field(column).type == pa.float64() for column in _FEATURE_COLUMNS
    )


def test_feature_builder_nulls_only_metrics_with_incomplete_symbol_history() -> None:
    access = _Access(missing_close_index=10, missing_row_index=58)

    frame = (
        TushareDailyBasicV1Builder()
        .build(
            access=cast(Access, access),
            trade_dates=access.dates,
        )
        .to_pandas()
    )

    assert pd.isna(frame.loc[0, "f_d_close_volatility_60d_asof_tminus1"])
    assert pd.isna(frame.loc[0, "f_d_close_return_5d_asof_tminus1"])
    assert pd.notna(frame.loc[0, "f_d_intraday_return"])


def test_feature_builder_declares_and_requires_its_exact_history_window() -> None:
    builder = TushareDailyBasicV1Builder()
    access = _Access()

    assert builder.lookback_sessions == 61
    with pytest.raises(ValueError, match="requires 62 trade dates"):
        builder.build(
            access=cast(Access, access),
            trade_dates=access.dates[1:],
        )
    with pytest.raises(TypeError, match="sequence of dates"):
        builder.build(
            access=cast(Access, access),
            trade_dates=access.dates[-1],
        )
