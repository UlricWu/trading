# filepath: tests/trading/backtest/test_context.py
"""Persistent-state tests for the daily-alpha backtest."""

from __future__ import annotations

from src.trading.backtest.context import BacktestState


def test_initial_state_is_complete_and_excludes_timing_scratch_values() -> None:
    state = BacktestState.initial(initial_cash=200_000)

    assert state.portfolio_state.cash == 200_000.0
    assert state.trade_dates == []
    assert not hasattr(state, "backtest_timing")
    assert not hasattr(state, "current_scores")
