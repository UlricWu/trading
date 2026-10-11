# filepath: src/workflows/backtest.py
"""Prepare one set of inputs and compose independent account replays."""
from __future__ import annotations

from pathlib import Path
from typing import cast

from src.access import Access
from src.config.backtest_config import RiskConfig
from src.jobs.requests import (
    BacktestSubmission, ExecutionEvalSubmission, FullBacktestSubmission,
    RiskRuleEvalSubmission, SignalEvalSubmission, StrategyEvalSubmission,
)
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.components import prepare_market_input, create_state, create_execution, prepare_portfolio_step
from src.trading.backtest.context import BacktestContext, BacktestResultContext, RiskComparisonContext
from src.trading.backtest.market.replay import ReplayMarket, ReplayMarketInput
from src.trading.backtest.market.signals import prepare_signals
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.bundle import ReportingBundle
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.backtest_layers import (
    AdvanceExecutionStep, AccountingStep, SignalStep, FixedHoldingStep,
    RiskDecisionStep, SubmitTargetsStep, RecordedOrdersStep,
)
from src.trading.backtest.steps.evaluation import (
    SignalEvaluationStep, RiskMetricsStep, ExecutionEvaluationStep,
    PortfolioEvaluationStep, BacktestSummaryStep, RiskComparisonStep,
)
from src.trading.backtest.steps.facts_persist import FactsPersistStep
from src.trading.backtest.steps.metrics_persist import MetricsPersistStep
from src.trading.backtest.steps.report import ReportStep
from src.trading.risk import RiskManager
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION


def run_backtest(
    *, submission: BacktestSubmission, path_manager: PathManager,
    experiment_name: str, metrics: BacktestMetrics,
) -> None:
    """Prepare shared inputs, then run one account or a matched risk comparison.

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
        _run_order_account(
            submission=submission,
            market_source=market_source,
            output_dir=output_dir,
            metrics=metrics,
        )
        return

    signal_inputs_by_ts_us = prepare_signals(
        submission.signal,
        start=submission.start,
        end=submission.end,
        access=access,
        pm=path_manager,
        min_listing_calendar_days=submission.min_listing_calendar_days,
        start_ts_us=submission.initial_account.ts_us,
        end_ts_us=submission.execution.end_ts_us,
    )
    prediction_dir = path_manager.experiment_backtest_predictions_dir(experiment_name=experiment_name)
    signal_step = SignalStep(signal_inputs_by_ts_us, prediction_dir)

    if isinstance(submission, RiskRuleEvalSubmission):
        _run_risk_comparison(
            submission=submission,
            market_source=market_source,
            signal_step=signal_step,
            path_manager=path_manager,
            output_dir=output_dir,
            metrics=metrics,
        )
        return

    risk = submission.risk if isinstance(submission, FullBacktestSubmission) else None
    _run_signal_account(
        submission=submission,
        market_source=market_source,
        signal_step=signal_step,
        risk=risk,
        path_manager=path_manager,
        output_dir=output_dir,
        metrics=metrics,
    )


def _run_signal_account(
    *,
    submission: SignalEvalSubmission | StrategyEvalSubmission | RiskRuleEvalSubmission | FullBacktestSubmission,
    market_source: ReplayMarketInput,
    signal_step: SignalStep,
    risk: RiskConfig | None,
    path_manager: PathManager,
    output_dir: Path,
    metrics: BacktestMetrics,
) -> BacktestResultContext:
    """Replay signals through a fresh strategy and account, then evaluate the completed facts."""
    initial_account = submission.initial_account
    execution_config = submission.execution
    strategy = submission.strategy

    state = create_state(initial_account)
    market = ReplayMarket(market_source)
    execution = create_execution(
        state=state, config=execution_config, quote_rule=strategy.quote,
    )
    portfolio_step = prepare_portfolio_step(
        strategy=strategy, market=market, execution=execution_config, initial=initial_account,
    )
    risk_steps: tuple[PipelineStep[BacktestContext], ...] = ()
    if risk is not None:
        risk_steps = (
            RiskDecisionStep(
                risk=RiskManager.from_config(risk), market=market,
                quantity_steps={symbol: rule.buy_step_qty for symbol, rule in execution_config.quantity_rules.items()},
            ),
        )

    initial_exit_ts_us = (
        tuple(portfolio_step.constructor.exit_due.values())
        if isinstance(portfolio_step, FixedHoldingStep) else ()
    )
    replay_ts_us = (
        *market_source.timestamps(),
        *signal_step.signal_inputs_by_ts_us,
        *execution_config.risk_check_ts_us,
        *initial_exit_ts_us,
        initial_account.ts_us,
        execution_config.end_ts_us,
    )
    clock = ReplayClock.from_iterable(
        replay_ts_us, start_us=initial_account.ts_us, end_us=execution_config.end_ts_us,
    )

    per_timing_steps: tuple[PipelineStep[BacktestContext], ...] = (
        AdvanceExecutionStep(execution, market),
        AccountingStep(market),
        signal_step,
        portfolio_step,
        *risk_steps,
        SubmitTargetsStep(
            execution=execution,
            market=market,
            windows=tuple(window for session in market_source.sessions for window in session.windows),
            order_window_minutes=cast(int, strategy.order_window_minutes),
        ),
        AccountingStep(market),
    )

    metrics.applied_risk = risk
    risk_evaluation_steps: tuple[PipelineStep[BacktestResultContext], ...] = ()
    if risk is None:
        metrics.not_run["risk"] = "no_business_risk"
    else:
        risk_evaluation_steps = (RiskMetricsStep(configured_rules=tuple(rule.type for rule in risk.rules)),)
    if execution_config.fill.type != "limit_price":
        reason = f"{execution_config.fill.type}_fills"
        metrics.not_run.update(price_feasibility=reason, market_liquidity=reason)

    final_steps: tuple[PipelineStep[BacktestResultContext], ...] = (
        FactsPersistStep(output_dir=output_dir),
        SignalEvaluationStep(path_manager),
        *risk_evaluation_steps,
        ExecutionEvaluationStep(),
        PortfolioEvaluationStep(annual_factor=execution_config.annual_factor),
        BacktestSummaryStep(),
        MetricsPersistStep(output_dir),
        ReportStep(ReportingBundle(output_dir)),
    )

    pipeline = BacktestPipeline(
        clock=clock,
        session_at=market_source.session_at,
        per_timing_steps=per_timing_steps,
        final_steps=final_steps,
        instrumentation=Instrumentation(output_dir.name),
    )
    return pipeline.run(state, metrics=metrics)


def _run_order_account(
    *, submission: ExecutionEvalSubmission, market_source: ReplayMarketInput,
    output_dir: Path, metrics: BacktestMetrics,
) -> BacktestResultContext:
    """Replay frozen order commands through a fresh account and evaluate its completed facts."""
    initial_account = submission.initial_account
    execution_config = submission.execution
    commands = OrderCommandSchedule.from_files(
        submission.order_refs,
        start=submission.start,
        end=submission.end,
        market=market_source,
        start_ts_us=initial_account.ts_us,
        end_ts_us=execution_config.end_ts_us,
    )

    state = create_state(initial_account)
    market = ReplayMarket(market_source)
    execution = create_execution(state=state, config=execution_config, quote_rule=None)

    replay_ts_us = (
        *market_source.timestamps(),
        *commands.timestamps(),
        initial_account.ts_us,
        execution_config.end_ts_us,
    )
    clock = ReplayClock.from_iterable(
        replay_ts_us, start_us=initial_account.ts_us, end_us=execution_config.end_ts_us,
    )

    per_timing_steps: tuple[PipelineStep[BacktestContext], ...] = (
        AdvanceExecutionStep(execution, market),
        AccountingStep(market),
        RecordedOrdersStep(execution, market, commands),
        AccountingStep(market),
    )

    metrics.applied_risk = None
    metrics.not_run.update(risk="no_business_risk", signal="frozen_commands", strategy="frozen_commands")
    final_steps: tuple[PipelineStep[BacktestResultContext], ...] = (
        FactsPersistStep(output_dir=output_dir),
        ExecutionEvaluationStep(),
        PortfolioEvaluationStep(annual_factor=execution_config.annual_factor),
        BacktestSummaryStep(),
        MetricsPersistStep(output_dir),
        ReportStep(ReportingBundle(output_dir)),
    )

    pipeline = BacktestPipeline(
        clock=clock,
        session_at=market_source.session_at,
        per_timing_steps=per_timing_steps,
        final_steps=final_steps,
        instrumentation=Instrumentation(output_dir.name),
    )
    return pipeline.run(state, metrics=metrics)


def _run_risk_comparison(
    *, submission: RiskRuleEvalSubmission, market_source: ReplayMarketInput,
    signal_step: SignalStep, path_manager: PathManager, output_dir: Path, metrics: BacktestMetrics,
) -> None:
    """Publish a comparison only after both independent account reports complete."""
    without_risk = _run_signal_account(
        submission=submission,
        market_source=market_source,
        signal_step=signal_step,
        risk=None,
        path_manager=path_manager,
        output_dir=output_dir / "without_risk",
        metrics=BacktestMetrics(mode=metrics.mode, parameters=metrics.parameters, not_run={}),
    )
    with_risk = _run_signal_account(
        submission=submission,
        market_source=market_source,
        signal_step=signal_step,
        risk=submission.risk,
        path_manager=path_manager,
        output_dir=output_dir / "with_risk",
        metrics=BacktestMetrics(mode=metrics.mode, parameters=metrics.parameters, not_run={}),
    )

    context = RiskComparisonContext(
        without_risk=without_risk.metrics, with_risk=with_risk.metrics, metrics=metrics,
    )
    steps: tuple[PipelineStep[RiskComparisonContext], ...] = (
        RiskComparisonStep(),
        MetricsPersistStep(output_dir),
        ReportStep(ReportingBundle(output_dir)),
    )
    with Instrumentation(output_dir.name) as instrumentation:
        run_steps(context=context, steps=steps, instrumentation=instrumentation)
