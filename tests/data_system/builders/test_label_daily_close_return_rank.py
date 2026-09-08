# filepath: tests/data_system/builders/test_label_daily_close_return_rank.py
"""Behavior tests for single-maturity close-return rank labels."""

from __future__ import annotations

from typing import cast

import pandas as pd
import pytest

from src.access import Access
from src.data_system.builders.label_daily_close_return_rank import (
    DailyCloseReturnRankV1Builder,
)


class _Access:
    def __init__(
        self,
        *,
        bars: dict[str, pd.DataFrame],
        factors: dict[str, pd.DataFrame],
    ) -> None:
        self._bars = bars
        self._factors = factors
        self.daily_dates: list[str] = []

    def daily_bars(self, *, trade_date: str) -> pd.DataFrame:
        self.daily_dates.append(trade_date)
        return self._bars[trade_date].copy()

    def adjustment_factors(self, *, trade_date: str) -> pd.DataFrame:
        return self._factors[trade_date].copy()


@pytest.mark.parametrize("horizon", [1, 3, 5])
def test_close_return_rank_has_one_schema_and_one_maturity(horizon: int) -> None:
    dates = tuple(
        pd.bdate_range("2026-07-01", periods=horizon + 1).strftime("%Y-%m-%d")
    )
    signal_date = dates[0]
    maturity_date = dates[-1]
    symbols = ["000001", "000002", "000003"]
    access = _Access(
        bars={
            signal_date: pd.DataFrame(
                {
                    "symbol": symbols,
                    "trade_date": [signal_date] * 3,
                    "close": [10.0, 10.0, 10.0],
                }
            ),
            maturity_date: pd.DataFrame(
                {
                    "symbol": symbols,
                    "trade_date": [maturity_date] * 3,
                    "close": [11.0, 12.0, 11.0],
                }
            ),
        },
        factors={
            signal_date: pd.DataFrame(
                {
                    "symbol": symbols,
                    "trade_date": [signal_date] * 3,
                    "adj_factor": [1.0, 1.0, 2.0],
                }
            ),
            maturity_date: pd.DataFrame(
                {
                    "symbol": symbols,
                    "trade_date": [maturity_date] * 3,
                    "adj_factor": [1.0, 1.0, 2.0],
                }
            ),
        },
    )

    result = DailyCloseReturnRankV1Builder(lookahead=horizon).build(
        access=cast(Access, access),
        trade_dates=dates,
    )

    assert result.column_names == ["symbol", "trade_date", "y_rank_return"]
    assert result["symbol"].to_pylist() == symbols
    assert result["trade_date"].to_pylist() == [signal_date] * 3
    assert result["y_rank_return"].to_pylist() == [0.5, 1.0, 0.5]
    assert access.daily_dates == [signal_date, maturity_date]


def test_close_return_rank_keeps_signal_rows_with_missing_maturity() -> None:
    signal_date = "2026-07-17"
    maturity_date = "2026-07-20"
    signal_symbols = ["000001", "000002", "000003"]
    maturity_symbols = signal_symbols[:2]
    access = _Access(
        bars={
            signal_date: pd.DataFrame(
                {
                    "symbol": signal_symbols,
                    "trade_date": [signal_date] * 3,
                    "close": [10.0, 10.0, 10.0],
                }
            ),
            maturity_date: pd.DataFrame(
                {
                    "symbol": maturity_symbols,
                    "trade_date": [maturity_date] * 2,
                    "close": [11.0, 12.0],
                }
            ),
        },
        factors={
            signal_date: pd.DataFrame(
                {
                    "symbol": signal_symbols,
                    "trade_date": [signal_date] * 3,
                    "adj_factor": [1.0, 1.0, 1.0],
                }
            ),
            maturity_date: pd.DataFrame(
                {
                    "symbol": maturity_symbols,
                    "trade_date": [maturity_date] * 2,
                    "adj_factor": [1.0, 1.0],
                }
            ),
        },
    )

    result = DailyCloseReturnRankV1Builder(lookahead=1).build(
        access=cast(Access, access),
        trade_dates=(signal_date, maturity_date),
    )

    assert result["y_rank_return"].to_pylist() == [0.5, 1.0, None]


def test_close_return_rank_rejects_invalid_lookahead_and_wrong_window() -> None:
    with pytest.raises(TypeError, match="must be an int"):
        DailyCloseReturnRankV1Builder(lookahead=True)
    with pytest.raises(ValueError, match="must be positive"):
        DailyCloseReturnRankV1Builder(lookahead=0)

    builder = DailyCloseReturnRankV1Builder(lookahead=3)
    with pytest.raises(ValueError, match="requires 4 trade dates"):
        builder.build(
            access=cast(Access, object()),
            trade_dates=("2026-07-17", "2026-07-20"),
        )
