# filepath: src/trading/portfolio/constructors/factory.py
from __future__ import annotations

from src.config.backtest_config import (
    StrategyConfig,
    ThresholdStrategyConfig,
    TopKHysteresisStrategyConfig,
)
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.constructors.threshold import ThresholdConstructor
from src.trading.portfolio.constructors.topk_hysteresis import TopKHysteresisConstructor


def build_portfolio_constructor(
    config: StrategyConfig,
) -> PortfolioConstructor:
    """Build one portfolio constructor from validated strategy config."""
    if isinstance(config, ThresholdStrategyConfig):
        return ThresholdConstructor(
            threshold=config.params.threshold,
            target_quantity=config.params.target_quantity,
        )
    if isinstance(config, TopKHysteresisStrategyConfig):
        return TopKHysteresisConstructor(
            max_positions=config.params.max_positions,
            entry_threshold=config.params.entry_threshold,
            exit_threshold=config.params.exit_threshold,
            rebalance_interval_minutes=config.params.rebalance_interval_minutes,
            keep_winners=config.params.keep_winners,
            target_quantity=config.params.target_quantity,
        )
    raise TypeError(f"unsupported strategy config: {type(config).__name__}")
