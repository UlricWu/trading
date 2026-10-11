# filepath: src/workflows/backtest.py
"""Compose independent evaluations and three shared-account replay workflows."""
from __future__ import annotations

from typing import cast

from src.access import Access
from src.jobs.requests import (
    SignalEvalSubmission, RiskRuleEvalSubmission, ExecutionEvalSubmission,
    StrategyEvalSubmission, FullBacktestSubmission,
)
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.components import prepare_market, create_state, create_execution, prepare_portfolio_step
from src.trading.backtest.context import (
    BacktestContext, BacktestResultContext, SignalEvaluationContext, RiskEvaluationContext,
)
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.backtest.execution.venue.fake import ReferenceVenue, LimitPriceVenue
from src.trading.backtest.market.signals import prepare_evaluation_signals, prepare_signals
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.bundle import ReportingBundle
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.backtest_layers import (
    PredictionGenerationStep, AdvanceExecutionStep, AccountingStep,
    SignalStep, FixedHoldingStep, RiskDecisionStep, SubmitTargetsStep, RecordedOrdersStep,
)
from src.trading.backtest.steps.evaluation import (
    SignalEvaluationStep, RiskEvaluationStep, RiskMetricsStep, ExecutionEvaluationStep,
    PortfolioEvaluationStep, BacktestSummaryStep,
)
from src.trading.backtest.steps.facts_persist import FactsPersistStep
from src.trading.backtest.steps.metrics_persist import MetricsPersistStep
from src.trading.backtest.steps.report import ReportStep
from src.trading.risk import RiskCases, RiskManager
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION


def run_signal_evaluation(
    *, submission: SignalEvalSubmission, path_manager: PathManager,
    experiment_name: str, metrics: BacktestMetrics,
) -> None:
    """Evaluate fixed signals without preparing any trading account or strategy.

    The caller supplies the experiment name and initial metrics for this submission.

    Example:
        metrics = BacktestMetrics(mode=submission.mode, parameters=parameters, not_run={})
        run_signal_evaluation(submission=submission, path_manager=pm,
            experiment_name="backtest_2026-10-11_signal-1", metrics=metrics)
    """
    metrics.not_run = {"strategy": "signal_only", "risk": "signal_only", "execution": "signal_only"}
    signal_inputs_by_ts_us = prepare_evaluation_signals(submission.signal, start=submission.start, end=submission.end,
        access=Access(pm=path_manager, processed_version=PROCESSED_VERSION), pm=path_manager,
        min_listing_calendar_days=submission.min_listing_calendar_days)
    steps: tuple[PipelineStep[SignalEvaluationContext], ...] = (
        PredictionGenerationStep(
            signal_inputs_by_ts_us=signal_inputs_by_ts_us,
            prediction_dir=path_manager.experiment_backtest_predictions_dir(experiment_name=experiment_name),
        ),
        SignalEvaluationStep(path_manager),
        MetricsPersistStep(path_manager, experiment_name),
        ReportStep(ReportingBundle(path_manager), experiment_name),
    )
    with Instrumentation(experiment_name) as instrumentation:
        run_steps(context=SignalEvaluationContext(metrics=metrics), steps=steps, instrumentation=instrumentation)


def run_risk_rule_evaluation(
    *, submission: RiskRuleEvalSubmission, path_manager: PathManager,
    experiment_name: str, metrics: BacktestMetrics,
) -> None:
    """Evaluate fixed pre-risk cases with the actual selected rule implementation.

    The caller supplies the experiment name and initial metrics for this submission.

    Example:
        metrics = BacktestMetrics(mode=submission.mode, parameters=parameters, not_run={})
        run_risk_rule_evaluation(submission=submission, path_manager=pm,
            experiment_name="backtest_2026-10-11_risk-1", metrics=metrics)
    """
    metrics.not_run = {"signal": "fixed_risk_cases", "strategy": "fixed_risk_cases", "execution": "fixed_risk_cases"}
    cases = RiskCases.from_files(submission.case_refs)
    steps: tuple[PipelineStep[RiskEvaluationContext], ...] = (
        RiskEvaluationStep(RiskManager.from_config(submission.risk)),
        RiskMetricsStep(configured_rules=tuple(rule.type for rule in submission.risk.rules)),
        MetricsPersistStep(path_manager, experiment_name),
        ReportStep(ReportingBundle(path_manager), experiment_name),
    )
    with Instrumentation(experiment_name) as instrumentation:
        run_steps(context=RiskEvaluationContext(cases=cases, metrics=metrics), steps=steps,
                  instrumentation=instrumentation)


def run_execution_evaluation(
    *, submission: ExecutionEvalSubmission, path_manager: PathManager,
    experiment_name: str, metrics: BacktestMetrics,
) -> None:
    """Replay frozen commands through the shared price-model execution path.

    The caller supplies the experiment name and initial metrics for this submission.

    Example:
        metrics = BacktestMetrics(mode=submission.mode, parameters=parameters, not_run={})
        run_execution_evaluation(submission=submission, path_manager=pm,
            experiment_name="backtest_2026-10-11_execution-1", metrics=metrics)
    """
    metrics.not_run = {"signal": "frozen_commands", "strategy": "frozen_commands", "risk": "frozen_commands"}
    initial_account = submission.initial_account
    execution_config = submission.execution
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)

    market = prepare_market(config=execution_config, initial=initial_account, access=access)
    commands = OrderCommandSchedule.from_files(
        submission.order_refs,
        start=submission.start,
        end=submission.end,
        market=market,
        start_ts_us=initial_account.ts_us,
        end_ts_us=execution_config.end_ts_us,
    )

    state = create_state(initial_account)
    venue = LimitPriceVenue(FixedBPSlippageModel(cast(float, execution_config.slippage_bp)))
    execution = create_execution(state=state, config=execution_config, venue=venue, quote_rule=None)

    replay_ts_us = (
        *market.timestamps(),
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
    )
    final_steps: tuple[PipelineStep[BacktestResultContext], ...] = (
        FactsPersistStep(pm=path_manager, experiment_name=experiment_name),
        ExecutionEvaluationStep(),
        BacktestSummaryStep(),
        MetricsPersistStep(path_manager, experiment_name),
        ReportStep(ReportingBundle(path_manager), experiment_name),
    )

    pipeline = BacktestPipeline(
        clock=clock,
        session_at=market.session_at,
        per_timing_steps=per_timing_steps,
        final_steps=final_steps,
        instrumentation=Instrumentation(experiment_name),
    )
    pipeline.run(state, metrics=metrics)


def run_strategy_evaluation(
    *, submission: StrategyEvalSubmission, path_manager: PathManager,
    experiment_name: str, metrics: BacktestMetrics,
) -> None:
    """Evaluate a strategy with its declared execution assumptions and no business risk.

    The caller supplies the experiment name and initial metrics for this submission.

    Example:
        metrics = BacktestMetrics(mode=submission.mode, parameters=parameters, not_run={})
        run_strategy_evaluation(submission=submission, path_manager=pm,
            experiment_name="backtest_2026-10-11_strategy-1", metrics=metrics)
    """
    metrics.not_run = {"signal_evaluation": "strategy_return_only", "risk": "strategy_only"}
    initial_account = submission.initial_account
    execution_config = submission.execution
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)

    market = prepare_market(config=execution_config, initial=initial_account, access=access)
    signal_inputs_by_ts_us = prepare_signals(
        submission.signal,
        start=submission.start,
        end=submission.end,
        access=access,
        pm=path_manager,
        min_listing_calendar_days=submission.min_listing_calendar_days,
        start_ts_us=initial_account.ts_us,
        end_ts_us=execution_config.end_ts_us,
    )

    state = create_state(initial_account)
    venue: ReferenceVenue | LimitPriceVenue
    if execution_config.slippage_bp is None:
        metrics.not_run["price_feasibility"] = "reference_fills"
        venue = ReferenceVenue()
        quote_rule = None
    else:
        venue = LimitPriceVenue(FixedBPSlippageModel(execution_config.slippage_bp))
        quote_rule = submission.strategy.quote
    execution = create_execution(state=state, config=execution_config, venue=venue, quote_rule=quote_rule)
    portfolio_step = prepare_portfolio_step(
        strategy=submission.strategy, market=market, execution=execution_config, initial=initial_account,
    )
    trading_windows = tuple(window for session in market.source.sessions for window in session.windows)

    initial_exit_ts_us: tuple[int, ...] = ()
    if isinstance(portfolio_step, FixedHoldingStep):
        initial_exit_ts_us = tuple(portfolio_step.constructor.exit_due.values())
    replay_ts_us = (
        *market.timestamps(),
        *signal_inputs_by_ts_us,
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
        SignalStep(
            signal_inputs_by_ts_us=signal_inputs_by_ts_us,
            prediction_dir=path_manager.experiment_backtest_predictions_dir(experiment_name=experiment_name),
        ),
        portfolio_step,
        SubmitTargetsStep(
            execution=execution,
            market=market,
            windows=trading_windows,
            order_window_minutes=cast(int, submission.strategy.order_window_minutes),
        ),
    )
    final_steps: tuple[PipelineStep[BacktestResultContext], ...] = (
        FactsPersistStep(pm=path_manager, experiment_name=experiment_name),
        PortfolioEvaluationStep(annual_factor=execution_config.annual_factor),
        BacktestSummaryStep(),
        MetricsPersistStep(path_manager, experiment_name),
        ReportStep(ReportingBundle(path_manager), experiment_name),
    )

    pipeline = BacktestPipeline(
        clock=clock,
        session_at=market.session_at,
        per_timing_steps=per_timing_steps,
        final_steps=final_steps,
        instrumentation=Instrumentation(experiment_name),
    )
    pipeline.run(state, metrics=metrics)


def run_full_backtest(
    *, submission: FullBacktestSubmission, path_manager: PathManager,
    experiment_name: str, metrics: BacktestMetrics,
) -> None:
    """Compose one signal/strategy/risk/execution loop and evaluate its saved facts.

    The caller supplies the experiment name and initial metrics for this submission.

    Example:
        metrics = BacktestMetrics(mode=submission.mode, parameters=parameters, not_run={})
        run_full_backtest(submission=submission, path_manager=pm,
            experiment_name="backtest_2026-10-11_full-1", metrics=metrics)
    """
    initial_account = submission.initial_account
    execution_config = submission.execution
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)

    market = prepare_market(config=execution_config, initial=initial_account, access=access)
    signal_inputs_by_ts_us = prepare_signals(
        submission.signal,
        start=submission.start,
        end=submission.end,
        access=access,
        pm=path_manager,
        min_listing_calendar_days=submission.min_listing_calendar_days,
        start_ts_us=initial_account.ts_us,
        end_ts_us=execution_config.end_ts_us,
    )

    state = create_state(initial_account)
    venue = LimitPriceVenue(FixedBPSlippageModel(cast(float, execution_config.slippage_bp)))
    execution = create_execution(
        state=state, config=execution_config, venue=venue, quote_rule=submission.strategy.quote,
    )
    risk = RiskManager.from_config(submission.risk)
    portfolio_step = prepare_portfolio_step(
        strategy=submission.strategy, market=market, execution=execution_config, initial=initial_account,
    )
    trading_windows = tuple(window for session in market.source.sessions for window in session.windows)
    quantity_steps = {symbol: rule.buy_step_qty for symbol, rule in execution_config.quantity_rules.items()}

    initial_exit_ts_us: tuple[int, ...] = ()
    if isinstance(portfolio_step, FixedHoldingStep):
        initial_exit_ts_us = tuple(portfolio_step.constructor.exit_due.values())
    replay_ts_us = (
        *market.timestamps(),
        *signal_inputs_by_ts_us,
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
        SignalStep(
            signal_inputs_by_ts_us=signal_inputs_by_ts_us,
            prediction_dir=path_manager.experiment_backtest_predictions_dir(experiment_name=experiment_name),
        ),
        portfolio_step,
        RiskDecisionStep(risk=risk, market=market, quantity_steps=quantity_steps),
        SubmitTargetsStep(
            execution=execution,
            market=market,
            windows=trading_windows,
            order_window_minutes=cast(int, submission.strategy.order_window_minutes),
        ),
    )
    final_steps: tuple[PipelineStep[BacktestResultContext], ...] = (
        FactsPersistStep(pm=path_manager, experiment_name=experiment_name),
        SignalEvaluationStep(path_manager),
        RiskMetricsStep(configured_rules=tuple(rule.type for rule in submission.risk.rules)),
        ExecutionEvaluationStep(),
        PortfolioEvaluationStep(annual_factor=execution_config.annual_factor),
        BacktestSummaryStep(),
        MetricsPersistStep(path_manager, experiment_name),
        ReportStep(ReportingBundle(path_manager), experiment_name),
    )

    pipeline = BacktestPipeline(
        clock=clock,
        session_at=market.session_at,
        per_timing_steps=per_timing_steps,
        final_steps=final_steps,
        instrumentation=Instrumentation(experiment_name),
    )
    pipeline.run(state, metrics=metrics)
