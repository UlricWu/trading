# filepath: src/workflows/backtest.py
"""Compose one daily-alpha backtest execution."""

from __future__ import annotations

from src.access import Access
from src.config.backtest_config import BacktestConfig
from src.jobs.requests import BacktestSubmission
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep
from src.trading.backtest.context import BacktestContext, BacktestState
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.steps.backtest_layers import (
    AccountingStep,
    ExecutionEvalStep,
    FullBacktestStep,
    PortfolioStep,
    RiskEvalStep,
    SignalEvalStep,
    SignalStep,
    TradableAlphaEvalStep,
)
from src.trading.backtest.steps.metrics_persist import MetricsPersistStep
from src.trading.backtest.steps.report import ReportStep
from src.trading.backtest.timing import resolve_backtest_timing
from src.trading.sim import components as sim_components
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION, require_new_experiment


def run_daily_alpha_backtest(
    *,
    backtest_config: BacktestConfig,
    path_manager: PathManager,
    submission: BacktestSubmission,
    experiment_id: str,
) -> None:
    """Run one accepted daily-alpha backtest submission.

    Example:
        run_daily_alpha_backtest(
            backtest_config=backtest_config,
            path_manager=path_manager,
            submission=backtest_submission,
            experiment_id="run-1",
        )
    """
    experiment_name = require_new_experiment(
        path_manager=path_manager,
        kind="backtest",
        start_date=submission.start,
        end_date=submission.end,
        experiment_id=experiment_id,
    )
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    open_dates = access.trade_dates(
        start_date=submission.start,
        end_date=submission.end,
    )
    timings = resolve_backtest_timing(open_dates=open_dates)
    if not timings:
        raise ValueError("[BacktestWorkflow] backtest timings are required")

    components = sim_components.build_components(
        mode=submission.mode,
        model_experiment=submission.model_experiment,
        strategy=submission.strategy,
        cfg=backtest_config,
        pm=path_manager,
    )
    per_timing_steps: tuple[PipelineStep[BacktestContext], ...] = (
        SignalStep(
            access=access,
            pm=path_manager,
            min_listing_calendar_days=backtest_config.min_listing_calendar_days,
            signal=components.signal,
            feature_set=components.feature_set,
            feature_version=components.feature_version,
            feature_names=components.feature_names,
        ),
        SignalEvalStep(access=access),
        TradableAlphaEvalStep(),
        PortfolioStep(
            constructor=components.constructor,
            target_capacity=components.target_capacity,
        ),
        RiskEvalStep(risk=components.risk),
        ExecutionEvalStep(execution=components.execution),
        AccountingStep(),
        FullBacktestStep(),
    )
    final_steps: tuple[PipelineStep[BacktestContext], ...] = (
        MetricsPersistStep(
            pm=path_manager,
            experiment_name=experiment_name,
        ),
        ReportStep(
            pm=path_manager,
            experiment_name=experiment_name,
        ),
    )
    state = BacktestState.initial(initial_cash=backtest_config.init_cash)
    pipeline = BacktestPipeline(
        timings=timings,
        per_timing_steps=per_timing_steps,
        final_steps=final_steps,
        instrumentation=Instrumentation(experiment_name),
    )
    pipeline.run(state)
