# filepath: src/workflows/backtest.py
"""Assemble the five run-level Steps of one declared backtest scenario."""
from __future__ import annotations

from src.access import Access
from src.jobs.requests import (
    BacktestSubmission, ExecutionEvalSubmission, FullBacktestSubmission, RiskRuleEvalSubmission,
)
from src.observability.instrumentation import Instrumentation
from src.trading.backtest.pipeline import BacktestPipeline
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.prepare_inputs import PrepareInputsStep
from src.trading.backtest.steps.replay_account import ReplayAccountStep
from src.trading.backtest.steps.facts_persist import FactsPersistStep
from src.trading.backtest.steps.evaluate_results import EvaluateResultsStep
from src.trading.backtest.steps.publish_results import PublishResultsStep
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION


def run_backtest(
    *, submission: BacktestSubmission, path_manager: PathManager,
    experiment_name: str, metrics: BacktestMetrics,
) -> None:
    """Bind scenario dependencies and execute the shared run-level chain once.

    The caller owns the experiment name and submission parameter snapshot.

    Example:
        run_backtest(submission=submission, path_manager=pm,
            experiment_name="backtest_2026-10-11_run", metrics=metrics)
    """
    output_dir = path_manager.experiment_dir(experiment_name=experiment_name)
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    strategy = None if isinstance(submission, ExecutionEvalSubmission) else submission.strategy
    risk = submission.risk if isinstance(submission, (RiskRuleEvalSubmission, FullBacktestSubmission)) else None
    steps = (
        PrepareInputsStep(submission=submission, access=access, pm=path_manager),
        ReplayAccountStep(
            initial_account=submission.initial_account, execution=submission.execution,
            strategy=strategy, risk=risk,
            prediction_dir=path_manager.experiment_backtest_predictions_dir(experiment_name=experiment_name),
        ),
        FactsPersistStep(output_dir=output_dir),
        EvaluateResultsStep(pm=path_manager, annual_factor=submission.execution.annual_factor, risk=risk),
        PublishResultsStep(output_dir=output_dir),
    )
    BacktestPipeline(steps=steps, instrumentation=Instrumentation(experiment_name)).run(metrics=metrics)
