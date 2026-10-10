# filepath: src/workflows/backtest.py
"""Compose one daily-alpha backtest execution."""

from __future__ import annotations

import json
from datetime import date, time
from time import monotonic

from src import logs
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
from src.trading.backtest.steps.facts_persist import FactsPersistStep
from src.trading.backtest.steps.metrics_persist import MetricsPersistStep
from src.trading.backtest.steps.report import ReportStep
from src.trading.backtest.timing import resolve_backtest_timing
from src.trading.sim import components as sim_components
from src.training.record import TrainingRunRecord
from src.utils.path import PathManager
from src.utils.datetime_utils import DateTimeUtils
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
    parameters_json = json.dumps({
        "mode": submission.mode.value,
        "start": submission.start,
        "end": submission.end,
        "model_ref": str(submission.model_ref),
        "strategy": submission.strategy.model_dump(mode="json"),
        "backtest_config": backtest_config.model_dump(mode="json"),
    }, ensure_ascii=False, allow_nan=False)
    parameter_labels = {
        "mode": "模式", "start": "开始日期", "end": "结束日期",
        "model_ref": "模型", "strategy": "策略", "backtest_config": "运行配置",
    }
    parameter_lines = "\n".join(
        f"{parameter_labels[key]} {key}={json.dumps(value, ensure_ascii=False, allow_nan=False)}"
        for key, value in json.loads(parameters_json).items()
    )
    logs.info(f"▶️ 回测任务; experiment={experiment_name}\n{parameter_lines}")
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    open_dates = access.trade_dates(
        start_date=submission.start,
        end_date=submission.end,
    )
    timings = resolve_backtest_timing(open_dates=open_dates)
    if not timings:
        raise ValueError("[BacktestWorkflow] backtest timings are required")

    training_schedule_ref = submission.model_ref.with_name("schedule.json")
    record = TrainingRunRecord.model_validate_json(
        training_schedule_ref.read_text(encoding="utf-8")
    )
    if record.model_ref != submission.model_ref:
        raise ValueError("model use requires the model_ref from the training record")
    first_decision = DateTimeUtils.local_time_to_utc_epoch_us(
        time(15, 0), date.fromisoformat(timings[0].signal_date),
    )
    if record.label_maturity_ts_us > first_decision:
        raise ValueError("model use requires training label maturity <= first backtest decision")

    components = sim_components.build_components(
        mode=submission.mode,
        model_ref=submission.model_ref,
        strategy=submission.strategy,
        cfg=backtest_config,
    )
    model = components.signal.model
    if model.train_dates != record.train_dates:
        raise ValueError("model train_dates disagree with the referenced training record")
    dataset = record.configuration.dataset
    if (
        model.feature_set, model.feature_version, model.feature_names,
        model.label_set, model.label_version, model.label_column,
    ) != (
        dataset.feature_set, dataset.feature_version, tuple(dataset.feature_columns),
        dataset.label_set, dataset.label_version, dataset.label_column,
    ):
        raise ValueError("model dataset disagrees with the referenced training record")
    if model.feature_set == "l2_stock_1430":
        raise ValueError("daily-close backtest cannot execute a 14:30 decision model")
    run_dir = path_manager.experiment_dir(experiment_name=experiment_name)
    logs.info(
        f"✅ 模型加载; first_decision={timings[0].signal_date}T15:00:00+08:00 "
        f"decision_days={len(timings)}"
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
        FactsPersistStep(pm=path_manager, experiment_name=experiment_name),
        MetricsPersistStep(
            pm=path_manager,
            experiment_name=experiment_name,
            parameters_json=parameters_json,
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
        instrumentation=Instrumentation(experiment_name, descriptions={
            "SignalStep": "信号生成与预测保存",
            "PortfolioStep": "目标持仓构造",
            "RiskEvalStep": "风控评价",
            "ExecutionEvalStep": "执行与成交评价",
            "AccountingStep": "账户估值",
            "SignalEvalStep": "信号与模型目标评价",
            "TradableAlphaEvalStep": "可交易信号评价",
            "FullBacktestStep": "回测状态汇总",
            "FactsPersistStep": "执行与权益明细保存",
            "MetricsPersistStep": "指标保存",
            "ReportStep": "报告输出",
        }),
        clock=monotonic,
    )
    pipeline.run(state)
