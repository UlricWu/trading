# filepath: tests/config/test_backtest_config.py
"""Schema and construction tests for public backtest strategies."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from src.config.backtest_config import (
    BacktestConfig,
    BacktestMode,
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


def test_backtest_requires_explicit_listing_age() -> None:
    config = BacktestConfig.model_validate(
        {
            "name": "daily_alpha",
            "dates": ["2026-05-06", "2026-05-07"],
            "strategy": {
                "type": "threshold",
                "params": {"threshold": 0.5},
            },
            "init_cash": 200_000,
            "backtest_mode": BacktestMode.FULL_BACKTEST,
            "min_listing_calendar_days": 120,
        }
    )

    assert config.min_listing_calendar_days == 120


def test_backtest_rejects_static_symbol_universe() -> None:
    with pytest.raises(ValidationError):
        BacktestConfig.model_validate(
            {
                "name": "daily_alpha",
                "dates": ["2026-05-06", "2026-05-07"],
                "symbols": ["000001"],
                "strategy": {
                    "type": "threshold",
                    "params": {"threshold": 0.5},
                },
                "init_cash": 200_000,
                "backtest_mode": BacktestMode.FULL_BACKTEST,
                "min_listing_calendar_days": 120,
            }
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("min_listing_calendar_days", -1),
        ("min_listing_calendar_days", True),
    ],
)
def test_backtest_rejects_invalid_universe_policy(
    field_name: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "name": "daily_alpha",
        "dates": ["2026-05-06", "2026-05-07"],
        "strategy": {
            "type": "threshold",
            "params": {"threshold": 0.5},
        },
        "init_cash": 200_000,
        "backtest_mode": BacktestMode.FULL_BACKTEST,
        "min_listing_calendar_days": 120,
    }
    payload[field_name] = value

    with pytest.raises(ValidationError):
        BacktestConfig.model_validate(payload)


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
