# filepath: tests/config/test_backtest_config.py
"""Schema and construction tests for public backtest strategies."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import TypeAdapter, ValidationError

from src.config.backtest_config import (
    FakeExecutionConfig,
    ReplayMarketSourceConfig,
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


@pytest.mark.parametrize("source", [
    {"frequency": "tick"}, {"start": "2026-7-17"}, {"end": "2026-07-16"},
    {"symbols": []}, {"symbols": ["600000", "600000"]}, {"symbols": ["830799"]},
    {"symbols": ["60000x"]}, {"symbols": [600000]},
    {"output_ref": "relative.json"}, {"output_ref": "/tmp/market.json"}, {"output_ref": None},
    {"guess_unit": True},
])
def test_replay_source_rejects_invalid_parameters(source: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReplayMarketSourceConfig.model_validate({"frequency": "minute", "start": "2026-07-17",
                                                "end": "2026-07-21", "symbols": ["600000"], **source})


@pytest.mark.parametrize("frequency", ["daily", "minute"])
def test_execution_selects_local_market_data(
    backtest_payload: Callable[[str], dict[str, object]], frequency: str,
) -> None:
    fields = backtest_payload("execution_eval")["execution"]
    fields["market_source"]["frequency"] = frequency

    config = FakeExecutionConfig.model_validate(fields)

    assert config.market_source.frequency == frequency
    assert config.market_source.symbols == ("600000",)


@pytest.mark.parametrize("invalid_input", ["missing", "null", "json_only", "json_with_source", "null_ref"])
def test_execution_requires_local_source_and_rejects_market_json(
    backtest_payload: Callable[[str], dict[str, object]], invalid_input: str,
) -> None:
    fields = backtest_payload("execution_eval")["execution"]
    if invalid_input in ("missing", "json_only"):
        fields.pop("market_source")
    elif invalid_input == "null":
        fields["market_source"] = None
    if invalid_input in ("json_only", "json_with_source", "null_ref"):
        fields["market_ref"] = None if invalid_input == "null_ref" else "/data/market.json"
    with pytest.raises(ValidationError) as caught:
        FakeExecutionConfig.model_validate(fields)
    errors = {error["loc"]: error["type"] for error in caught.value.errors()}
    if invalid_input in ("missing", "null", "json_only"):
        assert ("market_source",) in errors
    else:
        assert errors[("market_ref",)] == "extra_forbidden"
