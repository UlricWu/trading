# filepath: tests/trading/portfolio/constructors/test_factory.py
"""Construction tests for public backtest strategies."""

from __future__ import annotations

from pydantic import TypeAdapter

from src.config.backtest_config import StrategyConfig
from src.trading.portfolio.constructors.factory import build_portfolio_constructor
from src.trading.portfolio.constructors.threshold import ThresholdConstructor
from src.trading.portfolio.constructors.topk_hysteresis import (
    TopKHysteresisConstructor,
)


def test_threshold_config_constructs_the_runtime_strategy_directly() -> None:
    strategy = TypeAdapter(StrategyConfig).validate_python(
        {
            "type": "threshold",
            "params": {"threshold": 0.75, "target_quantity": 200},
        }
    )

    constructor = build_portfolio_constructor(strategy)

    assert isinstance(constructor, ThresholdConstructor)
    assert constructor.threshold == 0.75
    assert constructor.target_quantity == 200


def test_topk_config_constructs_the_runtime_strategy_directly() -> None:
    strategy = TypeAdapter(StrategyConfig).validate_python(
        {
            "type": "topk_hysteresis",
            "params": {
                "max_positions": 5,
                "entry_threshold": 0.7,
                "exit_threshold": 0.3,
                "rebalance_interval_minutes": 15,
                "keep_winners": True,
                "target_quantity": 300,
            },
        }
    )

    constructor = build_portfolio_constructor(strategy)

    assert isinstance(constructor, TopKHysteresisConstructor)
    assert constructor.max_positions == 5
    assert constructor.entry_threshold == 0.7
    assert constructor.exit_threshold == 0.3
    assert constructor.rebalance_interval_minutes == 15
    assert constructor.keep_winners is True
    assert constructor.target_quantity == 300
