# filepath: tests/trading/backtest/market/test_daily_signal_data.py
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from src.access import Access, meta
from src.trading.backtest.market.daily_signal_data import read_daily_raw_signal_view_data
from src.utils.path import PathManager


def _feature(pm: PathManager, day: str) -> None:
    path = pm.feature_object(feature_set="tushare_daily_basic", version="v1", trade_date="2026-07-01").payload_path
    path.parent.mkdir(parents=True)
    pd.DataFrame({"symbol": ["000001", "600000"], "trade_date": [day] * 2, "factor": [1.0, 2.0]}).to_parquet(path, index=False)
    meta.commit(pm=pm, payload_path=path)


def test_shared_feature_reader_preserves_requested_symbol_order_and_raw_prices(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _feature(pm, "2026-07-01")
    access = Mock(spec=Access)
    access.daily_bars.return_value = pd.DataFrame({"symbol": ["600000", "000001"], "close": [20.0, 10.0]})
    result = read_daily_raw_signal_view_data(access=access, pm=pm, symbols=("600000", "000001"), price_date="2026-07-02", feature_date="2026-07-01", feature_set="tushare_daily_basic", feature_version="v1", feature_names=("factor",))
    assert list(result) == ["symbol", "trade_date", "close", "factor"]
    assert result.to_dict("list") == {"symbol": ["600000", "000001"], "trade_date": ["2026-07-01"] * 2, "close": [20.0, 10.0], "factor": [2.0, 1.0]}
    access.daily_bars.assert_called_once_with(trade_date="2026-07-02", symbols=["600000", "000001"])


def test_wrong_feature_partition_date_cannot_be_used_for_replay(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    _feature(pm, "2026-07-02")
    access = Mock(spec=Access)
    access.daily_bars.return_value = pd.DataFrame({"symbol": ["000001"], "close": [10.0]})
    with pytest.raises(ValueError, match="partition date mismatch"):
        read_daily_raw_signal_view_data(access=access, pm=pm, symbols=("000001",), price_date="2026-07-02", feature_date="2026-07-01", feature_set="tushare_daily_basic", feature_version="v1", feature_names=("factor",))
