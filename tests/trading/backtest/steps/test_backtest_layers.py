# filepath: tests/trading/backtest/steps/test_backtest_layers.py
"""Explicit-result tests for daily-alpha backtest layers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from src.access import Access
from src.trading.backtest.context import BacktestState
from src.trading.backtest.steps import backtest_layers as layers
from src.trading.backtest.timing import BacktestTiming
from src.utils.path import PathManager


def test_signal_uses_the_signal_date_for_prices_universe_and_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = Mock(spec=Access)
    access.universe.return_value = ("000001",)
    read_prices = Mock(
        return_value=pd.DataFrame({"symbol": ["000001"], "close": [10.0]})
    )
    read_view = Mock(return_value=pd.DataFrame({"symbol": ["000001"]}))
    monkeypatch.setattr(layers, "read_raw_close", read_prices)
    monkeypatch.setattr(layers, "read_daily_raw_signal_view_data", read_view)

    data_view = Mock()
    monkeypatch.setattr(layers, "DailyView", Mock(return_value=data_view))
    bar = SimpleNamespace(
        should_trade=True,
        ts_us=1,
        trade_date="2026-07-01",
        symbols=["000001"],
        data_view=data_view,
    )
    session = SimpleNamespace(clock=object(), data_view=data_view)
    from_data_view = Mock(return_value=session)
    monkeypatch.setattr(
        layers,
        "ReplaySession",
        SimpleNamespace(from_data_view=from_data_view),
    )
    kernel = Mock()
    kernel.run.return_value = [bar]
    monkeypatch.setattr(layers, "BacktestKernel", Mock(return_value=kernel))
    signal_provider = Mock()
    signal_provider.scores.return_value = {}
    path_manager = Mock(spec=PathManager)
    operation = layers.SignalStep(
        access=access,
        pm=path_manager,
        min_listing_calendar_days=120,
        signal=signal_provider,
        feature_set="features",
        feature_version="v1",
        feature_names=("factor",),
    )

    result = operation(
        BacktestTiming(
            signal_date="2026-07-01",
            forward_date="2026-07-02",
        ),
        BacktestState.initial(initial_cash=200_000),
    )

    read_prices.assert_called_once_with(
        access=access,
        trade_date="2026-07-01",
        symbols=None,
    )
    access.universe.assert_called_once_with(
        trade_date="2026-07-01",
        min_listing_calendar_days=120,
    )
    read_view.assert_called_once_with(
        access=access,
        pm=path_manager,
        symbols=("000001",),
        price_date="2026-07-01",
        feature_date="2026-07-01",
        feature_set="features",
        feature_version="v1",
        feature_names=("factor",),
    )
    assert result.raw_prices == {"000001": 10.0}
    assert result.scores == {}
    assert result.skipped_symbols == ("000001",)


def test_portfolio_result_has_one_pre_risk_target_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor = Mock()
    constructor.targets.return_value = {"000001": 100}
    make_executable = Mock(return_value={"000001": 100})
    monkeypatch.setattr(layers, "make_executable_targets", make_executable)
    state = BacktestState.initial(initial_cash=200_000)
    signal = SimpleNamespace(
        bar=SimpleNamespace(ts_us=1),
        scores={"000001": 0.8},
        raw_prices={"000001": 10.0},
        skipped_symbols=("000002",),
    )

    result = layers.PortfolioStep(
        constructor=constructor,
        target_capacity=None,
    )(signal, state)

    assert result.targets == {"000001": 100}
    assert not hasattr(result, "raw_targets")
    make_executable.assert_called_once_with(
        raw_targets={"000001": 100},
        positions=state.portfolio_state.positions,
        current_raw_prices={"000001": 10.0},
        hold_symbols=("000002",),
        max_positions=None,
    )
