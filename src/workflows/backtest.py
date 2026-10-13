# filepath: src/workflows/backtest.py
"""Configure account dependencies and execute one shared backtest pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import cast

from src.access import Access
from src.config.backtest_config import RiskConfig
from src.jobs.requests import (
    BacktestSubmission, ExecutionEvalSubmission, FullBacktestSubmission, RiskRuleEvalSubmission,
)
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep
from src.trading.backtest.components import (
    OrderAccountComponents, SignalAccountComponents,
    build_order_account_components, build_signal_account_components, prepare_market_input, prepare_signal,
)
from src.trading.backtest.context import BacktestContext, BacktestResultContext
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.bundle import ReportingBundle
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.backtest_layers import (
    AdvanceExecutionStep, AccountingStep, FixedHoldingStep, PortfolioStep, RecordedOrdersStep,
    RiskDecisionStep, RiskOutcomeStep, SignalStep, SubmitTargetsStep,
)
from src.trading.backtest.steps.evaluation import (
    SignalEvaluationStep, ExecutionEvaluationStep, PortfolioEvaluationStep, BacktestSummaryStep,
    RiskMetricsStep,
)
from src.trading.backtest.steps.facts_persist import FactsPersistStep
from src.trading.backtest.steps.metrics_persist import MetricsPersistStep
from src.trading.backtest.steps.report import ReportStep
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION


def run_backtest(
    *, submission: BacktestSubmission, path_manager: PathManager,
    experiment_name: str, metrics: BacktestMetrics,
) -> None:
    """Run one declared scenario through the shared account pipeline.

    The caller owns the experiment name and submission parameter snapshot.

    Example:
        run_backtest(submission=submission, path_manager=pm,
            experiment_name="backtest_2026-10-11_run", metrics=metrics)
    """
    output_dir = path_manager.experiment_dir(experiment_name=experiment_name)
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    market_source = prepare_market_input(
        config=submission.execution,
        initial=submission.initial_account,
        access=access,
    )
    if isinstance(submission, ExecutionEvalSubmission):
        components = build_order_account_components(
            submission=submission, market_source=market_source, metrics=metrics,
        )
    else:
        signal = prepare_signal(
            submission=submission, access=access, pm=path_manager, experiment_name=experiment_name,
        )
        components = build_signal_account_components(
            submission=submission, market_source=market_source, signal=signal,
            risk=submission.risk if isinstance(submission, (RiskRuleEvalSubmission, FullBacktestSubmission)) else None,
            metrics=metrics,
        )

    _run_account(components=components, path_manager=path_manager, output_dir=output_dir, metrics=metrics)


def _run_account(
    *,
    components: SignalAccountComponents | OrderAccountComponents,
    path_manager: PathManager,
    output_dir: Path,
    metrics: BacktestMetrics,
) -> BacktestResultContext:
    """Insert source-specific decisions and evaluations into the common account chain."""
    decision_steps = _build_decision_steps(components=components)
    outcome_steps = _build_risk_outcome_steps(components=components)
    evaluation_steps = _build_evaluation_steps(components=components, path_manager=path_manager)

    per_timing_steps: tuple[PipelineStep[BacktestContext], ...] = (
        AdvanceExecutionStep(execution=components.execution, market=components.market),
        AccountingStep(market=components.market),
        *decision_steps,
        AccountingStep(market=components.market),
        *outcome_steps,
    )
    final_steps: tuple[PipelineStep[BacktestResultContext], ...] = (
        FactsPersistStep(output_dir=output_dir),
        *evaluation_steps,
        BacktestSummaryStep(),
        MetricsPersistStep(output_dir),
        ReportStep(ReportingBundle(output_dir)),
    )

    pipeline = BacktestPipeline(
        clock=components.clock,
        session_at=components.market.source.session_at,
        per_timing_steps=per_timing_steps,
        final_steps=final_steps,
        instrumentation=Instrumentation(output_dir.name),
    )
    return pipeline.run(components.state, metrics=metrics)


def _build_decision_steps(
    *, components: SignalAccountComponents | OrderAccountComponents,
) -> tuple[PipelineStep[BacktestContext], ...]:
    """Choose frozen commands or signal-driven targets, then bind their submission."""
    if isinstance(components, OrderAccountComponents):
        return (
            RecordedOrdersStep(
                execution=components.execution,
                market=components.market,
                commands=components.commands,
            ),
        )

    risk_steps: tuple[PipelineStep[BacktestContext], ...] = ()
    if components.risk is not None:
        risk_steps = (
            RiskDecisionStep(
                risk=components.risk,
                market=components.market,
                quantity_steps=components.quantity_steps,
            ),
        )

    return (
        SignalStep(signal=components.signal),
        _build_portfolio_step(components=components),
        *risk_steps,
        SubmitTargetsStep(
            execution=components.execution,
            market=components.market,
            windows=components.windows,
            order_window_minutes=components.order_window_minutes,
        ),
    )


def _build_portfolio_step(
    *, components: SignalAccountComponents,
) -> PipelineStep[BacktestContext]:
    """Select scheduled holding decisions or the configured portfolio constructor."""
    if isinstance(components.constructor, FixedHoldingConstructor):
        return FixedHoldingStep(
            constructor=components.constructor,
            market=components.market,
        )
    return PortfolioStep(
        constructor=components.constructor,
        market=components.market,
        target_capacity=components.target_capacity,
    )


def _build_risk_outcome_steps(
    *, components: SignalAccountComponents | OrderAccountComponents,
) -> tuple[PipelineStep[BacktestContext], ...]:
    """Observe the settled outcome only for accounts with risk decisions."""
    if isinstance(components, SignalAccountComponents) and components.risk is not None:
        return (RiskOutcomeStep(),)
    return ()


def _build_evaluation_steps(
    *, components: SignalAccountComponents | OrderAccountComponents, path_manager: PathManager,
) -> tuple[PipelineStep[BacktestResultContext], ...]:
    """Evaluate available signal and risk facts, followed by every account's execution and returns."""
    evaluation_steps: tuple[PipelineStep[BacktestResultContext], ...] = ()
    if isinstance(components, SignalAccountComponents):
        evaluation_steps = (SignalEvaluationStep(pm=path_manager),)
        if components.risk is not None:
            evaluation_steps += (
                RiskMetricsStep(config=cast(RiskConfig, components.risk_config)),
            )

    return (
        *evaluation_steps,
        ExecutionEvaluationStep(),
        PortfolioEvaluationStep(annual_factor=components.annual_factor),
    )
