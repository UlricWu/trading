# filepath: src/workflows/backtest.py
"""Compose independent evaluations and three shared-account replay workflows."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from datetime import date
from typing import cast

from pydantic import TypeAdapter

from src.access import Access
from src.config.backtest_config import FakeExecutionConfig, FixedHoldingStrategyConfig, StrategyConfig, InitialAccount
from src.jobs.requests import (
    SignalEvalSubmission, RiskRuleEvalSubmission, ExecutionEvalSubmission,
    StrategyEvalSubmission, FullBacktestSubmission,
)
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.components import prepare_market, create_state, build_risk
from src.trading.backtest.context import (
    BacktestContext, BacktestState, SignalEvaluationContext, RiskEvaluationContext,
)
from src.trading.backtest.execution.fake import FakeExecution
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.venue.fake import ReferenceVenue, LimitPriceVenue
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.utils.datetime_utils import DateTimeUtils
from src.trading.backtest.market.signals import prepare_signals, validate_recorded_model_uses
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.bundle import ReportingBundle
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.backtest_layers import (
    PredictionGenerationStep, AdvanceExecutionStep, AccountingStep,
    SignalStep, PortfolioStep, FixedHoldingStep, RiskDecisionStep, SubmitTargetsStep, RecordedOrdersStep,
)
from src.trading.backtest.steps.evaluation import (
    SignalEvaluationStep, RiskEvaluationStep, RiskMetricsStep, ExecutionEvaluationStep,
    PortfolioEvaluationStep, BacktestSummaryStep,
)
from src.trading.backtest.steps.facts_persist import FactsPersistStep
from src.trading.backtest.steps.metrics_persist import MetricsPersistStep
from src.trading.backtest.steps.report import ReportStep
from src.trading.core.events import OrderIntent, CancelOrders
from src.trading.execution.policies.constraints import QuoteRule
from src.trading.portfolio.constructors.factory import build_portfolio_constructor
from src.trading.portfolio.constructors.topk_hysteresis import TopKHysteresisConstructor
from src.trading.risk.evaluation import read_risk_cases
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
    batches = prepare_signals(submission.signal, start=submission.start, end=submission.end,
        access=Access(pm=path_manager, processed_version=PROCESSED_VERSION), pm=path_manager,
        min_listing_calendar_days=submission.min_listing_calendar_days)
    steps: tuple[PipelineStep[SignalEvaluationContext], ...] = (
        PredictionGenerationStep(
            batches=batches,
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
    cases = read_risk_cases(submission.case_refs)
    steps: tuple[PipelineStep[RiskEvaluationContext], ...] = (
        RiskEvaluationStep(build_risk(submission.risk)),
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
    config = submission.execution
    market = prepare_market(config=config, initial=submission.initial_account,
                            access=Access(pm=path_manager, processed_version=PROCESSED_VERSION))
    commands = tuple(command for ref in submission.order_refs
        for command in TypeAdapter(tuple[OrderIntent | CancelOrders, ...]).validate_json(ref.read_bytes(), strict=True))
    if any(a.ts_us > b.ts_us for a, b in zip(commands, commands[1:])):
        raise ValueError("frozen order/cancel commands must retain chronological order")
    by_time: dict[int, list[OrderIntent | CancelOrders]] = defaultdict(list)
    for command in commands:
        if not submission.start <= market.session_at(command.ts_us).trade_date <= submission.end:
            raise ValueError("frozen command date is outside the selected request range")
        if not submission.initial_account.ts_us <= command.ts_us <= config.end_ts_us:
            raise ValueError("frozen command is outside the simulation interval")
        by_time[command.ts_us].append(command)
    state = create_state(submission.initial_account)
    execution = _execution(state, config,
        LimitPriceVenue(FixedBPSlippageModel(cast(float, config.slippage_bp))), None)
    clock = ReplayClock.from_iterable((*market.timestamps(), *by_time,
        submission.initial_account.ts_us, config.end_ts_us),
        start_us=submission.initial_account.ts_us, end_us=config.end_ts_us)
    per_timing_steps: tuple[PipelineStep[BacktestContext], ...] = (
        AdvanceExecutionStep(execution, market), AccountingStep(market),
        RecordedOrdersStep(execution, market, {ts: tuple(items) for ts, items in by_time.items()}),
    )
    BacktestPipeline(clock=clock, session_at=market.session_at, per_timing_steps=per_timing_steps,
        final_steps=(
            FactsPersistStep(pm=path_manager, experiment_name=experiment_name),
            ExecutionEvaluationStep(), BacktestSummaryStep(),
            MetricsPersistStep(path_manager, experiment_name), ReportStep(ReportingBundle(path_manager), experiment_name),
        ), instrumentation=Instrumentation(experiment_name)).run(state, metrics=metrics)


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
    config = submission.execution
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    market = prepare_market(config=config, initial=submission.initial_account, access=access)
    batches = prepare_signals(submission.signal, start=submission.start, end=submission.end,
        access=access, pm=path_manager, min_listing_calendar_days=submission.min_listing_calendar_days)
    validate_recorded_model_uses(batches)
    _validate_signal_times(batches.keys(), submission.initial_account.ts_us, config.end_ts_us)
    state = create_state(submission.initial_account)
    if config.slippage_bp is None:
        metrics.not_run["price_feasibility"] = "reference_fills"
        execution = _execution(state, config, ReferenceVenue(), None)
    else:
        execution = _execution(state, config,
            LimitPriceVenue(FixedBPSlippageModel(config.slippage_bp)), submission.strategy.quote)
    portfolio_step = _portfolio_step(submission.strategy, market, config, submission.initial_account)
    windows = tuple(window for session in market.source.sessions for window in session.windows)
    clock = ReplayClock.from_iterable((*market.timestamps(), *batches,
        *(portfolio_step.constructor.exit_due.values() if isinstance(portfolio_step, FixedHoldingStep) else ()),
        submission.initial_account.ts_us, config.end_ts_us),
        start_us=submission.initial_account.ts_us, end_us=config.end_ts_us)
    per_timing_steps: tuple[PipelineStep[BacktestContext], ...] = (
        AdvanceExecutionStep(execution, market), AccountingStep(market),
        SignalStep(
            batches=batches,
            prediction_dir=path_manager.experiment_backtest_predictions_dir(experiment_name=experiment_name),
        ),
        portfolio_step,
        SubmitTargetsStep(execution, market, windows, cast(int, submission.strategy.order_window_minutes)),
    )
    BacktestPipeline(clock=clock, session_at=market.session_at, per_timing_steps=per_timing_steps,
        final_steps=(
            FactsPersistStep(pm=path_manager, experiment_name=experiment_name),
            PortfolioEvaluationStep(annual_factor=config.annual_factor), BacktestSummaryStep(),
            MetricsPersistStep(path_manager, experiment_name), ReportStep(ReportingBundle(path_manager), experiment_name),
        ), instrumentation=Instrumentation(experiment_name)).run(state, metrics=metrics)


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
    config = submission.execution
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    market = prepare_market(config=config, initial=submission.initial_account, access=access)
    batches = prepare_signals(submission.signal, start=submission.start, end=submission.end,
        access=access, pm=path_manager, min_listing_calendar_days=submission.min_listing_calendar_days)
    validate_recorded_model_uses(batches)
    _validate_signal_times(batches.keys(), submission.initial_account.ts_us, config.end_ts_us)
    state = create_state(submission.initial_account)
    execution = _execution(state, config,
        LimitPriceVenue(FixedBPSlippageModel(cast(float, config.slippage_bp))), submission.strategy.quote)
    risk = build_risk(submission.risk)
    portfolio_step = _portfolio_step(submission.strategy, market, config, submission.initial_account)
    windows = tuple(window for session in market.source.sessions for window in session.windows)
    clock = ReplayClock.from_iterable((*market.timestamps(), *batches, *config.risk_check_ts_us,
        *(portfolio_step.constructor.exit_due.values() if isinstance(portfolio_step, FixedHoldingStep) else ()),
        submission.initial_account.ts_us, config.end_ts_us),
        start_us=submission.initial_account.ts_us, end_us=config.end_ts_us)
    per_timing_steps: tuple[PipelineStep[BacktestContext], ...] = (
        AdvanceExecutionStep(execution, market), AccountingStep(market),
        SignalStep(
            batches=batches,
            prediction_dir=path_manager.experiment_backtest_predictions_dir(experiment_name=experiment_name),
        ),
        portfolio_step,
        RiskDecisionStep(risk, market, {symbol: rule.buy_step_qty for symbol, rule in config.quantity_rules.items()}),
        SubmitTargetsStep(execution, market, windows, cast(int, submission.strategy.order_window_minutes)),
    )
    BacktestPipeline(clock=clock, session_at=market.session_at, per_timing_steps=per_timing_steps,
        final_steps=(
            FactsPersistStep(pm=path_manager, experiment_name=experiment_name), SignalEvaluationStep(path_manager),
            RiskMetricsStep(configured_rules=tuple(rule.type for rule in submission.risk.rules)),
            ExecutionEvaluationStep(), PortfolioEvaluationStep(annual_factor=config.annual_factor),
            BacktestSummaryStep(), MetricsPersistStep(path_manager, experiment_name),
            ReportStep(ReportingBundle(path_manager), experiment_name),
        ), instrumentation=Instrumentation(experiment_name)).run(state, metrics=metrics)


def _execution(state: BacktestState, config: FakeExecutionConfig,
               venue: ReferenceVenue | LimitPriceVenue, quote: QuoteRule | None) -> FakeExecution:
    fees = config.fees
    settlement = SettlementEngine(cost_model=AShareCostModel(
        fees.commission_rate, fees.stamp_tax_rate, fees.transfer_rate, fees.min_commission))
    return FakeExecution(state.portfolio_state, state.execution_ledger, state.pending_orders,
                         settlement, venue, config.quantity_rules, quote)


def _validate_signal_times(times: Iterable[int], start_us: int, end_us: int) -> None:
    if any(not start_us <= ts < end_us for ts in times):
        raise ValueError("selected signal decisions must be within the simulation interval and precede its end")


def _portfolio_step(strategy: StrategyConfig, market: ReplayMarket,
                    execution: FakeExecutionConfig, initial: InitialAccount) -> PortfolioStep | FixedHoldingStep:
    if isinstance(strategy, FixedHoldingStrategyConfig):
        params = strategy.params
        if params.initial_exit_dates.keys() != {s for s, qty in initial.positions.items() if qty}:
            raise ValueError("fixed_holding initial positions require explicit exit dates")
        deadlines = {symbol: DateTimeUtils.local_time_to_utc_epoch_us(params.exit_time, date.fromisoformat(day))
                     for symbol, day in params.initial_exit_dates.items()}
        for deadline in deadlines.values():
            market.session_at(deadline)
            if deadline < initial.ts_us:
                raise ValueError("initial exit plan precedes account snapshot")
        return FixedHoldingStep(FixedHoldingConstructor(params.max_positions, params.decision_time,
            params.exit_time, tuple(s.trade_date for s in market.source.sessions),
            execution.quantity_rules, deadlines), market)
    constructor = build_portfolio_constructor(strategy)
    capacity = constructor.max_positions if isinstance(constructor, TopKHysteresisConstructor) else None
    return PortfolioStep(constructor, market, capacity)
