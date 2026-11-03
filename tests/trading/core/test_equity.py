# filepath: tests/trading/core/test_equity.py
import pytest

from src.trading.core.equity import EquityCurve


def test_replacing_current_point_restores_prior_peak_and_drawdown() -> None:
    curve = EquityCurve()
    curve.append(ts_us=1, cash=100, market_value=0)
    curve.append(ts_us=2, cash=50, market_value=0)
    assert curve.max_drawdown == -0.5
    curve.append(ts_us=2, cash=110, market_value=0)
    assert curve.peak_equity == 110
    assert curve.max_drawdown == 0
    curve.append(ts_us=3, cash=99, market_value=0)
    curve.append(ts_us=3, cash=88, market_value=0)
    assert curve.max_drawdown == pytest.approx(-0.2)
    assert curve.peak_equity == 110
    assert len(curve.points) == 3
