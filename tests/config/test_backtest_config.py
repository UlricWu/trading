# filepath: tests/config/test_backtest_config.py
"""Schema and construction tests for public backtest strategies."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from src.config.backtest_config import (
    StrategyConfig,
    ThresholdStrategyConfig,
    TopKHysteresisStrategyConfig,
)


def test_threshold_strategy_applies_confirmed_default() -> None:
    strategy = TypeAdapter(StrategyConfig).validate_python(
        {"type": "threshold", "params": {"threshold": 0.5}}
    )

    assert isinstance(strategy, ThresholdStrategyConfig)
    assert strategy.params.threshold == 0.5
    assert strategy.params.target_quantity == 100


def test_topk_strategy_applies_confirmed_defaults() -> None:
    strategy = TypeAdapter(StrategyConfig).validate_python(
        {
            "type": "topk_hysteresis",
            "params": {
                "max_positions": 10,
                "entry_threshold": 0.5,
                "exit_threshold": 0.2,
            },
        }
    )

    assert isinstance(strategy, TopKHysteresisStrategyConfig)
    assert strategy.params.rebalance_interval_minutes == 1
    assert strategy.params.keep_winners is False
    assert strategy.params.target_quantity == 100


@pytest.mark.parametrize(
    "strategy",
    [
        {"type": "threshold", "params": {"threshold": float("nan")}},
        {"type": "threshold", "params": {"threshold": "0.5"}},
        {
            "type": "threshold",
            "params": {"threshold": 0.5, "target_quantity": 0},
        },
        {
            "type": "threshold",
            "params": {"threshold": 0.5, "qty": 100},
        },
        {
            "type": "topk_hysteresis",
            "params": {
                "max_positions": 0,
                "entry_threshold": 0.5,
                "exit_threshold": 0.2,
            },
        },
        {
            "type": "topk_hysteresis",
            "params": {
                "max_positions": 10,
                "entry_threshold": 0.2,
                "exit_threshold": 0.5,
            },
        },
        {
            "type": "topk_hysteresis",
            "params": {
                "max_positions": 10,
                "entry_threshold": 0.5,
                "exit_threshold": 0.2,
                "rebalance_interval_minutes": 0,
            },
        },
        {
            "type": "topk_hysteresis",
            "params": {
                "max_positions": 10,
                "entry_threshold": 0.5,
                "exit_threshold": 0.2,
                "keep_winners": 1,
            },
        },
        {"type": "unknown", "params": {}},
        {
            "type": "threshold",
            "params": {"threshold": 0.5},
            "extra": True,
        },
    ],
)
def test_strategy_rejects_values_outside_the_confirmed_schema(
    strategy: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(StrategyConfig).validate_python(strategy)
