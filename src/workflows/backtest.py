# filepath: src/workflows/backtest.py
"""Compose independent evaluations and three shared-account replay workflows."""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from datetime import date
from typing import cast

from pydantic import TypeAdapter

from src import logs
from src.access import Access
from src.config.backtest_config import BacktestConfig, FakeExecutionConfig, FixedHoldingStrategyConfig, StrategyConfig, InitialAccount
from src.jobs.requests import (
    BacktestSubmission, SignalEvalSubmission, RiskEvalSubmission, ExecutionEvalSubmission,
    TradableAlphaEvalSubmission, FullBacktestSubmission,
)
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep, run_steps
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.components import prepare_market, create_state, build_risk
from src.trading.backtest.context import (
    BacktestContext, BacktestState, SignalEvaluationContext, RiskEvaluationContext,
)
from src.trading.backtest.evaluation import evaluate_signals, evaluate_risk, evaluate_execution, evaluate_portfolio
from src.trading.backtest.execution.fake import FakeExecution
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.venue.fake import ReferenceVenue, LimitPriceVenue
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.utils.datetime_utils import DateTimeUtils
from src.trading.backtest.market.signals import prepare_signals, produce_signal, validate_recorded_model_uses
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.bundle import ReportingBundle
from src.trading.backtest.reporting.metrics import AccountResult, BacktestMetrics
from src.trading.backtest.steps.backtest_layers import (
    SignalEvaluationStep, RiskEvaluationStep, AdvanceExecutionStep, AccountingStep,
    SignalStep, PortfolioStep, FixedHoldingStep, RiskDecisionStep, SubmitTargetsStep, RecordedOrdersStep,
)
from src.trading.backtest.steps.facts_persist import FactsPersistStep
from src.trading.backtest.steps.metrics_persist import persist_backtest_metrics
from src.trading.core.events import OrderIntent, CancelOrders
from src.trading.execution.policies.constraints import QuoteRule
from src.trading.portfolio.constructors.factory import build_portfolio_constructor
from src.trading.portfolio.constructors.topk_hysteresis import TopKHysteresisConstructor
from src.trading.risk.evaluation import read_risk_cases
from src.training.evaluation import evaluate_prediction_records
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION, require_new_experiment


def run_signal_evaluation(
    *, submission: SignalEvalSubmission, path_manager: PathManager,
    experiment_id: str, backtest_config: BacktestConfig,
) -> None:
    """Evaluate fixed signals without preparing any trading account or strategy.

    Example:
        run_signal_evaluation(submission=submission, path_manager=pm,
                              experiment_id="signal-1", backtest_config=config)
    """
    name, metrics = _start(submission, path_manager, experiment_id, backtest_config)
    batches = prepare_signals(submission.signal, start=submission.start, end=submission.end,
        access=Access(pm=path_manager, processed_version=PROCESSED_VERSION), pm=path_manager,
        min_listing_calendar_days=backtest_config.min_listing_calendar_days)
    path_manager.experiment_dir(experiment_name=name).mkdir(parents=True, exist_ok=False)
    produced = tuple(produce_signal(batch, prediction_dir=path_manager.experiment_dir(experiment_name=name) / "predictions")
                     for batch in batches.values())
    context = SignalEvaluationContext(tuple(batch.prediction_ref for batch in produced),
                                     {batch.event.ts_us: batch.trade_date for batch in produced})
    with Instrumentation(name) as instrumentation:
        run_steps(context=context, steps=(SignalEvaluationStep(path_manager),), instrumentation=instrumentation)
    metrics.signal_eval = evaluate_signals(context.observations, decision_dates=context.decision_dates)
    metrics.prediction_refs = tuple(str(ref) for ref in context.prediction_refs)
    metrics.not_run = {"strategy": "signal_only", "risk": "signal_only", "execution": "signal_only"}
    _publish(metrics, path_manager, name)


def run_risk_evaluation(
    *, submission: RiskEvalSubmission, path_manager: PathManager, experiment_id: str,
) -> None:
    """Evaluate fixed pre-risk cases with the actual selected rule implementation.

    Example:
        run_risk_evaluation(submission=submission, path_manager=pm, experiment_id="risk-1")
    """
    name, metrics = _start(submission, path_manager, experiment_id)
    cases = read_risk_cases(submission.case_refs)
    path_manager.experiment_dir(experiment_name=name).mkdir(parents=True, exist_ok=False)
    context = RiskEvaluationContext(cases)
    with Instrumentation(name) as instrumentation:
        run_steps(context=context, steps=(RiskEvaluationStep(build_risk(submission.risk)),),
                  instrumentation=instrumentation)
    metrics.risk_eval = evaluate_risk(context.observations,
                                     configured_rules=tuple(rule.type for rule in submission.risk.rules))
    metrics.not_run = {"signal": "fixed_risk_cases", "strategy": "fixed_risk_cases", "execution": "fixed_risk_cases"}
    _publish(metrics, path_manager, name)


def run_execution_evaluation(
    *, submission: ExecutionEvalSubmission, path_manager: PathManager, experiment_id: str,
) -> None:
    """Replay frozen commands through the shared price-model execution path.

    Example:
        run_execution_evaluation(submission=submission, path_manager=pm, experiment_id="execution-1")
    """
    name, metrics = _start(submission, path_manager, experiment_id)
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
    path_manager.experiment_dir(experiment_name=name).mkdir(parents=True, exist_ok=False)
    BacktestPipeline(clock=clock, session_at=market.session_at, per_timing_steps=per_timing_steps,
        final_steps=(FactsPersistStep(pm=path_manager, experiment_name=name),),
        instrumentation=Instrumentation(name)).run(state)
    metrics.execution_eval = evaluate_execution(state.execution_ledger)
    metrics.final_account = _account_result(state)
    metrics.not_run = {"signal": "frozen_commands", "strategy": "frozen_commands", "risk": "frozen_commands"}
    _publish(metrics, path_manager, name)


def run_tradable_alpha_evaluation(
    *, submission: TradableAlphaEvalSubmission, path_manager: PathManager,
    experiment_id: str, backtest_config: BacktestConfig,
) -> None:
    """Evaluate a declared strategy using reference fills and a run-owned account.

    Example:
        run_tradable_alpha_evaluation(submission=submission, path_manager=pm,
                                     experiment_id="strategy-1", backtest_config=config)
    """
    name, metrics = _start(submission, path_manager, experiment_id, backtest_config)
    config = submission.execution
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    market = prepare_market(config=config, initial=submission.initial_account, access=access)
    batches = prepare_signals(submission.signal, start=submission.start, end=submission.end,
        access=access, pm=path_manager, min_listing_calendar_days=backtest_config.min_listing_calendar_days)
    validate_recorded_model_uses(batches)
    _validate_signal_times(batches.keys(), submission.initial_account.ts_us, config.end_ts_us)
    state = create_state(submission.initial_account)
    execution = _execution(state, config, ReferenceVenue(), None)
    portfolio_step = _portfolio_step(submission.strategy, market, config, submission.initial_account)
    windows = tuple(window for session in market.source.sessions for window in session.windows)
    clock = ReplayClock.from_iterable((*market.timestamps(), *batches,
        *(portfolio_step.constructor.exit_due.values() if isinstance(portfolio_step, FixedHoldingStep) else ()),
        submission.initial_account.ts_us, config.end_ts_us),
        start_us=submission.initial_account.ts_us, end_us=config.end_ts_us)
    per_timing_steps: tuple[PipelineStep[BacktestContext], ...] = (
        AdvanceExecutionStep(execution, market), AccountingStep(market),
        SignalStep(batches, path_manager.experiment_dir(experiment_name=name) / "predictions"),
        portfolio_step,
        SubmitTargetsStep(execution, market, windows, cast(int, submission.strategy.order_window_minutes)),
    )
    path_manager.experiment_dir(experiment_name=name).mkdir(parents=True, exist_ok=False)
    BacktestPipeline(clock=clock, session_at=market.session_at, per_timing_steps=per_timing_steps,
        final_steps=(FactsPersistStep(pm=path_manager, experiment_name=name),),
        instrumentation=Instrumentation(name)).run(state)
    metrics.portfolio = evaluate_portfolio(state.equity_curve, daily_equity=state.daily_equity,
        ledger=state.execution_ledger, annual_factor=config.annual_factor)
    metrics.final_account = _account_result(state)
    metrics.prediction_refs = tuple(str(ref) for ref in state.prediction_refs)
    metrics.not_run = {"signal_evaluation": "strategy_return_only", "risk": "reference_strategy",
                       "price_feasibility": "reference_fills"}
    _publish(metrics, path_manager, name)


def run_full_backtest(
    *, submission: FullBacktestSubmission, path_manager: PathManager,
    experiment_id: str, backtest_config: BacktestConfig,
) -> None:
    """Compose one signal/strategy/risk/execution loop and evaluate its saved facts.

    Example:
        run_full_backtest(submission=submission, path_manager=pm,
                          experiment_id="full-1", backtest_config=config)
    """
    name, metrics = _start(submission, path_manager, experiment_id, backtest_config)
    config = submission.execution
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    market = prepare_market(config=config, initial=submission.initial_account, access=access)
    batches = prepare_signals(submission.signal, start=submission.start, end=submission.end,
        access=access, pm=path_manager, min_listing_calendar_days=backtest_config.min_listing_calendar_days)
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
        SignalStep(batches, path_manager.experiment_dir(experiment_name=name) / "predictions"),
        portfolio_step,
        RiskDecisionStep(risk, market, {symbol: rule.buy_step_qty for symbol, rule in config.quantity_rules.items()}),
        SubmitTargetsStep(execution, market, windows, cast(int, submission.strategy.order_window_minutes)),
    )
    path_manager.experiment_dir(experiment_name=name).mkdir(parents=True, exist_ok=False)
    BacktestPipeline(clock=clock, session_at=market.session_at, per_timing_steps=per_timing_steps,
        final_steps=(FactsPersistStep(pm=path_manager, experiment_name=name),),
        instrumentation=Instrumentation(name)).run(state)
    metrics.signal_eval = evaluate_signals(
        evaluate_prediction_records(state.prediction_refs, pm=path_manager), decision_dates=state.decision_dates)
    metrics.risk_eval = evaluate_risk(state.risk_observations,
                                    configured_rules=tuple(rule.type for rule in submission.risk.rules))
    metrics.execution_eval = evaluate_execution(state.execution_ledger)
    metrics.portfolio = evaluate_portfolio(state.equity_curve, daily_equity=state.daily_equity,
        ledger=state.execution_ledger, annual_factor=config.annual_factor)
    metrics.final_account = _account_result(state)
    metrics.prediction_refs = tuple(str(ref) for ref in state.prediction_refs)
    _publish(metrics, path_manager, name)


def _start(submission: BacktestSubmission, pm: PathManager, experiment_id: str,
           backtest_config: BacktestConfig | None = None) -> tuple[str, BacktestMetrics]:
    PathManager.require_experiment_id(experiment_id)
    if isinstance(submission, RiskEvalSubmission):
        name = f"backtest_risk_{experiment_id}"
    else:
        name = require_new_experiment(path_manager=pm, kind="backtest", start_date=submission.start,
                                      end_date=submission.end, experiment_id=experiment_id)
    if pm.experiment_dir(experiment_name=name).exists():
        raise FileExistsError(f"experiment already exists: {name}")
    parameters = submission.model_dump(mode="json")
    if backtest_config is not None:
        parameters["backtest_config"] = backtest_config.model_dump(mode="json")
    labels = (("mode", "模式"), ("start", "开始日期"), ("end", "结束日期"),
              ("signal", "信号输入"), ("case_refs", "风控案例"), ("order_refs", "委托输入"),
              ("strategy", "策略"), ("initial_account", "初始账户"), ("risk", "风控"),
              ("execution", "执行"), ("kind", "任务"), ("backtest_config", "运行配置"))
    summary = "\n".join(f"{label} {key}={json.dumps(parameters[key], ensure_ascii=False)}"
                        for key, label in labels if key in parameters)
    logs.info(f"▶️ 回测任务; experiment={name}\n{summary}")
    return name, BacktestMetrics(mode=submission.mode, parameters=parameters, not_run={})


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


def _account_result(state: BacktestState) -> AccountResult:
    account = state.portfolio_state
    return AccountResult(cash=account.cash, positions=account.positions,
        sellable={symbol: account.t1_sellable(symbol) for symbol in account.positions},
        average_cost=account.avg_cost, trade_date=cast(str, account.current_day),
        equity=state.equity_curve.points[-1].equity, pending_order_ids=tuple(state.pending_orders))


def _publish(metrics: BacktestMetrics, pm: PathManager, name: str) -> None:
    persist_backtest_metrics(metrics=metrics, pm=pm, experiment_name=name)
    ReportingBundle(pm).run(experiment_name=name)


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
