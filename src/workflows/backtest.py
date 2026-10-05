# filepath: src/workflows/backtest.py
"""Compose one daily-alpha backtest execution."""

from __future__ import annotations

import hashlib
import json
from datetime import date, time

from src import logs
from src.access import Access
from src.config.backtest_config import BacktestConfig
from src.jobs.requests import BacktestSubmission, TrainingSelection
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
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
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

    use = submission.model_use
    schedule = json.loads(use.training_schedule_ref.read_text(encoding="utf-8"))
    selected = [entry for entry in schedule if entry["model_ref"] == str(submission.model_ref)]
    if len(selected) != 1:
        raise ValueError("model use requires exactly one matching training schedule entry")
    selection = TrainingSelection.model_validate({
        key: value for key, value in selected[0].items() if key != "model_ref"
    })
    first_decision = DateTimeUtils.local_time_to_utc_epoch_us(
        time(15, 0), date.fromisoformat(timings[0].signal_date),
    )
    if not selection.training_cutoff_ts_us <= use.effective_ts_us <= first_decision:
        raise ValueError("model use requires training cutoff <= effective time <= first decision")

    components = sim_components.build_components(
        mode=submission.mode,
        model_ref=submission.model_ref,
        strategy=submission.strategy,
        cfg=backtest_config,
    )
    model = components.signal.model
    if model.train_dates != selection.train_dates:
        raise ValueError("model train_dates disagree with the referenced training schedule")
    if model.feature_set == "l2_stock_1430":
        raise ValueError("daily-close backtest cannot execute a 14:30 decision model")
    with submission.model_ref.open("rb") as source:
        model_digest = hashlib.file_digest(source, "sha256").hexdigest()
    run_dir = path_manager.experiment_backtest_dir(experiment_name=experiment_name)
    FileSystem.write_bytes_atomic(
        run_dir / "binding.json",
        json.dumps({
            "event": "model_loaded_and_bound",
            "model_ref": str(submission.model_ref),
            "model_sha256": model_digest,
            "model_use": use.model_dump(mode="json"),
            "training_selection": selection.model_dump(mode="json"),
            "first_decision_ts_us": first_decision,
            "mode": submission.mode.value,
            "strategy": submission.strategy.model_dump(mode="json"),
            "backtest_config": backtest_config.model_dump(mode="json"),
        }, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"),
    )
    logs.info(
        f"✅ model binding; model_ref={submission.model_ref} model_sha256={model_digest} "
        f"effective_ts_us={use.effective_ts_us} first_decision_ts_us={first_decision}"
    )
    per_timing_steps: tuple[PipelineStep[BacktestContext], ...] = (
        SignalStep(
            access=access,
            pm=path_manager,
            min_listing_calendar_days=backtest_config.min_listing_calendar_days,
            signal=components.signal,
            model_ref=submission.model_ref,
            prediction_dir=run_dir / "predictions",
            feature_set=components.feature_set,
            feature_version=components.feature_version,
            feature_names=components.feature_names,
        ),
        PortfolioStep(
            constructor=components.constructor,
            target_capacity=components.target_capacity,
        ),
        RiskEvalStep(risk=components.risk),
        ExecutionEvalStep(execution=components.execution),
        AccountingStep(),
        SignalEvalStep(access=access, pm=path_manager),
        TradableAlphaEvalStep(),
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
