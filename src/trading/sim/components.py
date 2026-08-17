# filepath: src/trading/sim/components.py
"""Backtest runtime component construction."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass

from src import logs
from src.config.backtest_config import (
    BacktestConfig,
    BacktestMode,
    StrategyConfig,
)
from src.trading.execution.engine import ExecutionOrchestrator
from src.trading.execution.ideal import IdealExecution
from src.trading.execution.models.cost_a_share import AShareCostModel
from src.trading.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.execution.policies.constraints import AShareTargetClippingPolicy
from src.trading.execution.policies.validation import AShareOrderValidation
from src.trading.execution.settlement import SettlementEngine
from src.trading.execution.venue.sim_immediate import SimImmediateVenue
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.constructors.factory import build_portfolio_constructor
from src.trading.portfolio.constructors.topk_hysteresis import TopKHysteresisConstructor
from src.trading.risk.engine import NoOpRiskManager
from src.trading.signal.diagnostics import BasicSignalDiagnostics
from src.trading.signal.model import ModelSignalProvider
from src.training.artifact import load_inference_model
from src.utils.path import PathManager


@dataclass(frozen=True)
class Components:
    """Runtime components consumed by the fixed backtest layer graph.

    Example:
        components = build_components(
            mode=BacktestMode.FULL_BACKTEST,
            model_experiment="training-1",
            strategy=strategy,
            cfg=backtest_config,
            pm=path_manager,
        )
    """

    signal: ModelSignalProvider
    feature_set: str
    feature_version: str
    feature_names: tuple[str, ...]
    constructor: PortfolioConstructor
    risk: NoOpRiskManager
    execution: IdealExecution | ExecutionOrchestrator
    target_capacity: int | None


def build_components(
    *,
    mode: BacktestMode,
    model_experiment: str,
    strategy: StrategyConfig,
    cfg: BacktestConfig,
    pm: PathManager,
) -> Components:
    """Build artifact-backed components from static config and submission data.

    Example:
        components = build_components(
            mode=BacktestMode.FULL_BACKTEST,
            model_experiment="training-1",
            strategy=strategy,
            cfg=backtest_config,
            pm=path_manager,
        )
    """

    inference_model = load_inference_model(
        pm=pm,
        experiment_name=model_experiment,
    )

    constructor = build_portfolio_constructor(strategy)
    signal = ModelSignalProvider(
        model=inference_model,
        diagnostics=BasicSignalDiagnostics(
            log_daily_only=True,
            score_thresholds=_score_thresholds_for_constructor(constructor),
        ),
    )

    execution: IdealExecution | ExecutionOrchestrator
    if mode in {
        BacktestMode.SIGNAL_EVAL,
        BacktestMode.TRADABLE_ALPHA_EVAL,
        BacktestMode.RISK_EVAL,
    }:
        execution = IdealExecution()
    else:
        cost_model = AShareCostModel()
        execution = ExecutionOrchestrator(
            clip_policy=AShareTargetClippingPolicy(),
            validator=AShareOrderValidation(),
            cost_model=cost_model,
            venue=SimImmediateVenue(slippage_model=FixedBPSlippageModel(bp=5.0)),
            settlement=SettlementEngine(cost_model=cost_model),
        )

    logs.info(
        f"ready "
        f"mode={mode.value} "
        f"signal={type(signal).__name__} "
        f"feature_set={inference_model.feature_set} "
        f"feature_version={inference_model.feature_version} "
        f"feature_count={len(inference_model.feature_names)} "
        f"constructor={type(constructor).__name__} "
        f"constructor_params={_constructor_log_params(constructor)} "
        f"execution={type(execution).__name__}"
    )
    return Components(
        signal=signal,
        feature_set=inference_model.feature_set,
        feature_version=inference_model.feature_version,
        feature_names=inference_model.feature_names,
        constructor=constructor,
        risk=NoOpRiskManager(),
        execution=execution,
        target_capacity=_target_capacity_for_constructor(constructor),
    )


def _constructor_log_params(constructor: PortfolioConstructor) -> str:
    """Return public constructor params for one-line operational diagnostics."""
    if not is_dataclass(constructor):
        return "none"

    parts: list[str] = []
    for field in fields(constructor):
        if field.name.startswith("_"):
            continue
        value = getattr(constructor, field.name)
        parts.append(f"{field.name}:{value}")
    return ",".join(parts) or "none"


def _score_thresholds_for_constructor(
    constructor: PortfolioConstructor,
) -> tuple[float, ...]:
    """Expose strategy score cutoffs to score-distribution diagnostics."""
    threshold_names = ("threshold", "entry_threshold")
    out: list[float] = []
    for name in threshold_names:
        if not hasattr(constructor, name):
            continue
        out.append(float(getattr(constructor, name)))
    return tuple(out)


def _target_capacity_for_constructor(
    constructor: PortfolioConstructor,
) -> int | None:
    """Return the execution target capacity exposed by the constructor provider."""
    if isinstance(constructor, TopKHysteresisConstructor):
        return int(constructor.max_positions)
    return None
