# filepath: src/workflows/backtest.py
"""Declare startup, per-tick and result Steps for one backtest scenario."""
from __future__ import annotations

from typing import cast

from src.access import Access
from src.jobs.requests import (
    BacktestSubmission, ExecutionEvalSubmission, FullBacktestSubmission, RiskRuleEvalSubmission,
)
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep
from src.trading.backtest.context import BacktestContext
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.prepare_inputs import PrepareInputsStep
from src.trading.backtest.steps.initialize_account import InitializeAccountStep
from src.trading.backtest.steps.update_market import UpdateMarketStep
from src.trading.backtest.steps.advance_orders import AdvanceOrdersStep
from src.trading.backtest.steps.value_account import ValueAccountStep
from src.trading.backtest.steps.generate_signal import GenerateSignalStep
from src.trading.backtest.steps.generate_targets import GenerateTargetsStep
from src.trading.backtest.steps.apply_risk import ApplyRiskStep
from src.trading.backtest.steps.submit_targets import SubmitTargetsStep
from src.trading.backtest.steps.submit_frozen_commands import SubmitFrozenCommandsStep
from src.trading.backtest.steps.complete_risk_observation import CompleteRiskObservationStep
from src.trading.backtest.steps.schedule_wakeups import ScheduleWakeupsStep
from src.trading.backtest.steps.facts_persist import FactsPersistStep
from src.trading.backtest.steps.evaluate_results import EvaluateResultsStep
from src.trading.backtest.steps.publish_results import PublishResultsStep
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION


def run_backtest(
    *, submission: BacktestSubmission, path_manager: PathManager,
    experiment_name: str, metrics: BacktestMetrics,
) -> None:
    """Select decision Steps and bind their parameters before the Pipeline executes them.

    The caller owns the experiment name and submission parameter snapshot.

    Example:
        run_backtest(submission=submission, path_manager=pm,
            experiment_name="backtest_2026-10-11_run", metrics=metrics)
    """
    output_dir = path_manager.experiment_dir(experiment_name=experiment_name)
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    strategy = None if isinstance(submission, ExecutionEvalSubmission) else submission.strategy

    risk = submission.risk if isinstance(submission, (RiskRuleEvalSubmission, FullBacktestSubmission)) else None

    decision_steps: tuple[PipelineStep[BacktestContext], ...] = (
        (SubmitFrozenCommandsStep(),) if isinstance(submission, ExecutionEvalSubmission) else (
            GenerateSignalStep(
                prediction_dir=path_manager.experiment_backtest_predictions_dir(experiment_name=experiment_name),
            ),
            GenerateTargetsStep(),
            *((ApplyRiskStep(),) if risk is not None else ()),
            SubmitTargetsStep(order_window_minutes=cast(int, submission.strategy.order_window_minutes)),
        )
    )
    pipeline = BacktestPipeline(
        startup_steps=(
            PrepareInputsStep(submission=submission, access=access, pm=path_manager),
            InitializeAccountStep(initial_account=submission.initial_account, execution=submission.execution,
                                  strategy=strategy, risk=risk),
        ),
        tick_steps=(
            UpdateMarketStep(),
            AdvanceOrdersStep(),
            ValueAccountStep(),
            *decision_steps,
            ValueAccountStep(),
            *((CompleteRiskObservationStep(),) if risk is not None else ()),
            ScheduleWakeupsStep(),
        ),
        result_steps=(
            FactsPersistStep(output_dir=output_dir),
            EvaluateResultsStep(pm=path_manager, annual_factor=submission.execution.annual_factor, risk=risk),
            PublishResultsStep(output_dir=output_dir),
        ),
        instrumentation=Instrumentation(experiment_name),
    )
    pipeline.run(metrics=metrics)
