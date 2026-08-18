# filepath: tests/trading/sim/test_components.py
"""Runtime-source and mode-mapping tests for backtest components."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest

from src.config.backtest_config import BacktestConfig, BacktestMode
from src.jobs.requests import create_backtest_submission
from src.trading.sim import components as components_module
from src.utils.path import PathManager


@pytest.mark.parametrize(
    ("mode", "uses_ideal_execution"),
    [
        (BacktestMode.SIGNAL_EVAL, True),
        (BacktestMode.TRADABLE_ALPHA_EVAL, True),
        (BacktestMode.RISK_EVAL, True),
        (BacktestMode.EXECUTION_EVAL, False),
        (BacktestMode.FULL_BACKTEST, False),
    ],
)
def test_mode_selects_fixed_execution_and_noop_risk(
    mode: BacktestMode,
    uses_ideal_execution: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inference_model = SimpleNamespace(
        feature_set="features",
        feature_version="v1",
        feature_names=("factor",),
    )
    load_model = Mock(return_value=inference_model)
    constructor = object()
    monkeypatch.setattr(
        components_module,
        "load_inference_model",
        load_model,
    )
    monkeypatch.setattr(
        components_module,
        "build_portfolio_constructor",
        Mock(return_value=constructor),
    )
    monkeypatch.setattr(
        components_module,
        "ModelSignalProvider",
        Mock(return_value=object()),
    )
    monkeypatch.setattr(
        components_module,
        "BasicSignalDiagnostics",
        Mock(return_value=object()),
    )
    risk = object()
    monkeypatch.setattr(
        components_module,
        "NoOpRiskManager",
        Mock(return_value=risk),
    )
    ideal_execution = object()
    ideal_factory = Mock(return_value=ideal_execution)
    monkeypatch.setattr(components_module, "IdealExecution", ideal_factory)
    orchestrated_execution = object()
    orchestrator_factory = Mock(return_value=orchestrated_execution)
    monkeypatch.setattr(
        components_module,
        "ExecutionOrchestrator",
        orchestrator_factory,
    )
    for dependency_name in (
        "AShareCostModel",
        "AShareTargetClippingPolicy",
        "AShareOrderValidation",
        "SettlementEngine",
        "SimImmediateVenue",
    ):
        monkeypatch.setattr(
            components_module,
            dependency_name,
            Mock(return_value=object()),
        )
    slippage_factory = Mock(return_value=object())
    monkeypatch.setattr(
        components_module,
        "FixedBPSlippageModel",
        slippage_factory,
    )
    submission = create_backtest_submission(
        mode=mode.value,
        start="2026-07-01",
        end="2026-07-02",
        model_experiment="training-runtime",
        strategy={"type": "threshold", "params": {"threshold": 0.5}},
    )

    components = components_module.build_components(
        mode=submission.mode,
        model_experiment=submission.model_experiment,
        strategy=submission.strategy,
        cfg=BacktestConfig(
            init_cash=200_000,
            min_listing_calendar_days=120,
        ),
        pm=Mock(spec=PathManager),
    )

    load_model.assert_called_once_with(
        pm=ANY,
        experiment_name="training-runtime",
    )
    assert components.risk is risk
    if uses_ideal_execution:
        assert components.execution is ideal_execution
        ideal_factory.assert_called_once_with()
        slippage_factory.assert_not_called()
    else:
        assert components.execution is orchestrated_execution
        orchestrator_factory.assert_called_once()
        slippage_factory.assert_called_once_with(bp=5.0)
