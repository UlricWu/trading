# filepath: tests/trading/backtest/market/test_daily_view.py
from collections.abc import Sequence

import numpy as np
import pandas as pd
import pytest

from src.trading.backtest.market.daily_view import DailyView


def test_daily_view_owns_its_prices_and_features() -> None:
    values = np.array([[10.0, 0.5], [20.0, 0.8]])
    frame = pd.DataFrame(values, columns=("close", "factor"), copy=False)
    frame.insert(0, "symbol", ["A", "B"])
    view = DailyView(frame, trade_date="2026-07-03", price_column="close", ts_us=1)

    values[:] = -1
    view.on_time(1)

    np.testing.assert_array_equal(view.get_price_vector(("B", "A")), [20.0, 10.0])
    np.testing.assert_array_equal(view.get_feature_matrix(("B", "A")), [[0.8], [0.5]])


@pytest.mark.parametrize("symbols", [("A", "B"), ["B", "A"], ()])
def test_daily_queries_return_independent_arrays(symbols: Sequence[str]) -> None:
    frame = pd.DataFrame({"symbol": ["A", "B"], "close": [10.0, 20.0], "factor": [0.5, 0.8]})
    view = DailyView(frame, trade_date="2026-07-03", price_column="close", ts_us=1)
    view.on_time(1)
    prices = view.get_price_vector(symbols)
    features = view.get_feature_matrix(symbols)

    prices[:] = -1
    features[:] = -1

    np.testing.assert_array_equal(view.get_price_vector(("A", "B")), [10.0, 20.0])
    np.testing.assert_array_equal(view.get_feature_matrix(("A", "B")), [[0.5], [0.8]])
    assert prices.shape == (len(symbols),)
    assert features.shape == (len(symbols), 1)
