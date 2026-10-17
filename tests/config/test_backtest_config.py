# filepath: tests/config/test_backtest_config.py
"""Schema and construction tests for public backtest strategies."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import TypeAdapter, ValidationError

from src.config.backtest_config import (
    FakeExecutionConfig,
    InitialAccount,
    ReplayMarketSourceConfig,
    StrategyConfig,
    ThresholdStrategyConfig,
    TopKHysteresisStrategyConfig,
)


@pytest.mark.parametrize("recognition,sellable,error", [(False, 200, "recognition"), (True, 201, "unlisted")])
def test_snapshot_cannot_lose_recognized_rights_or_unlock_unlisted_shares(
    recognition: bool, sellable: int, error: str,
) -> None:
    rights = {"distribution": {"action_id": "600000/bonus", "symbol": "600000", "source_ref": "test",
        "announced_ts_us": 1, "record_ts_us": 2, "ex_ts_us": 3, "pay_ts_us": 5,
        "shares_available_ts_us": 5, "cash_per_share": "0.1", "shares_per_share": "0.5",
        "reference_cash_per_share": "0.1", "reference_shares_per_share": "0.5"},
        "eligible_qty": 200, "recognized": recognition}
    with pytest.raises(ValidationError, match=error):
        InitialAccount(ts_us=4, trade_date="2026-09-03", cash=0, positions={"600000": 300},
                       sellable={"600000": sellable}, average_cost={"600000": 10}, peak_equity=3000,
                       distribution_entitlements=[rights])


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
    {"symbols": None}, {"symbols": ["600000", "600000"]}, {"symbols": ["830799"]},
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


@pytest.mark.parametrize("selection", [{}, {"symbols": []}])
def test_market_source_defaults_to_full_market(selection: dict[str, object]) -> None:
    source = ReplayMarketSourceConfig.model_validate({
        "frequency": "daily", "start": "2026-09-01", "end": "2026-09-30", **selection,
    })

    assert source.symbols == ()


def test_execution_accepts_default_rules_and_complete_symbol_overrides(
    backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    fields = backtest_payload("execution_eval")["execution"]
    default = {"buy_min_qty": 100, "buy_step_qty": 100, "sell_min_qty": 1,
               "sell_step_qty": 1, "fill_step_qty": 1, "price_tick": "0.01", "enforce_t1": True}
    fields["quantity_rules"] = {"default": default, "overrides": {
        "688001": {**default, "buy_min_qty": 200, "buy_step_qty": 1},
    }}

    config = FakeExecutionConfig.model_validate(fields)

    assert config.quantity_rules.default.buy_min_qty == 100
    assert config.quantity_rules.overrides["688001"].buy_min_qty == 200


@pytest.mark.parametrize("invalid_rules", [
    {}, {"overrides": {}}, {"600000": {}}, {"default": None},
    {"default": {}, "overrides": {"600000": {"buy_min_qty": 200}}},
])
def test_execution_requires_explicit_complete_default_rules(
    backtest_payload: Callable[[str], dict[str, object]], invalid_rules: dict[str, object],
) -> None:
    fields = backtest_payload("execution_eval")["execution"]
    fields["quantity_rules"] = invalid_rules
    with pytest.raises(ValidationError):
        FakeExecutionConfig.model_validate(fields)


def test_quantity_rule_overrides_replace_whole_rules(
    backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    fields = backtest_payload("execution_eval")["execution"]
    fields["quantity_rules"]["overrides"] = {"600000": {"buy_min_qty": 200}}
    with pytest.raises(ValidationError, match="buy_step_qty"):
        FakeExecutionConfig.model_validate(fields)


def test_execution_rejects_old_per_symbol_rule_shape(
    backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    fields = backtest_payload("execution_eval")["execution"]
    fields["quantity_rules"] = {"600000": fields["quantity_rules"]["default"]}
    with pytest.raises(ValidationError) as caught:
        FakeExecutionConfig.model_validate(fields)
    errors = {error["loc"]: error["type"] for error in caught.value.errors()}
    assert errors[("quantity_rules", "default")] == "missing"
    assert errors[("quantity_rules", "600000")] == "extra_forbidden"
