# filepath: src/workflows/offline_training.py
"""Compose one selected model fit and optional fixed-model daily evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, time
from time import monotonic

from src import logs
from src.access import Access
from src.config.model_config import FeatureGroupConfig, FeatureLabelConfig, ModelConfig, ModelPreset
from src.data_system.builders.previous_day import PREOPEN_AVAILABLE_TIME, PREOPEN_DECISION_TIME
from src.data_system.builders.registry import get_feature_builder, get_label_builder
from src.data_system.builders.stock_1430 import STOCK_1430_DECISION_TIME
from src.jobs.requests import TrainingSubmission
from src.observability.instrumentation import Instrumentation
from src.pipeline import PipelineStep
from src.training.context import EvaluationDay, TrainingContext, TrainingWindow
from src.training.engines.report import build_training_start_log
from src.training.models.catalog import get_model_trainer
from src.training.pipeline import TrainingPipeline
from src.training.record import TrainingRunRecord
from src.training.top_selection import save_top_selection_evaluation
from src.training.steps.artifact_persist import ArtifactPersistStep
from src.training.steps.dataset_build import DatasetBuildStep
from src.training.steps.ic_evaluate import ICEvaluateStep
from src.training.steps.metrics_persist import MetricsPersistStep
from src.training.steps.model_train import ModelTrainStep
from src.training.steps.prediction import PredictionStep
from src.training.steps.preprocess import PreprocessStep
from src.training.steps.report import ReportStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager
from src.workflows import PROCESSED_VERSION, require_new_experiment


def run_offline_training(
    *,
    models: Mapping[str, ModelPreset],
    feature_groups: Mapping[str, FeatureGroupConfig],
    path_manager: PathManager,
    submission: TrainingSubmission,
    experiment_id: str,
) -> None:
    """Split chronological validation, fit once, and evaluate the frozen model.

    Label maturity is a theoretical information boundary. Current committed
    inputs and this offline run do not establish historical file availability.

    Example:
        run_offline_training(
            models=app_config.models, feature_groups=app_config.feature_groups,
            path_manager=path_manager, submission=training_submission,
            experiment_id="run-1",
        )
    """
    try:
        preset = models[submission.model]
    except KeyError as exc:
        raise ValueError(f"model preset is not configured: {submission.model}") from exc
    try:
        feature = feature_groups[submission.dataset.feature_group]
    except KeyError as exc:
        raise ValueError(f"feature group is not configured: {submission.dataset.feature_group}") from exc
    trainer = get_model_trainer(preset.group)
    label_set, label_version = submission.dataset.label_group.split("/")
    if (feature.feature_set, feature.feature_version) == ("l2_stock_1430", "v1"):
        decision_time = STOCK_1430_DECISION_TIME
    elif (feature.feature_set, feature.feature_version) == ("previous_day_daily_l2", "v1"):
        decision_time = PREOPEN_DECISION_TIME
    else:
        get_feature_builder(feature.feature_set, feature.feature_version)
        decision_time = time(15, 0)
    if (label_set, label_version) == ("l2_stock_1430_t1_vwap_rank", "v1"):
        label_column = "y_rank_return"
        lookahead = 1
        maturity_time = time(14, 36)
        target_description = "当日至下一交易日 [14:31, 14:36) VWAP 窗口的复权收益横截面百分位排名"
    elif label_set in {"next_day_high_return_rank", "next_day_high_top_decile"}:
        label_column = "y_event" if label_set == "next_day_high_top_decile" else "y_rank_return"
        lookahead = 1
        maturity_time = PREOPEN_AVAILABLE_TIME
        target_description = "当日 [14:31,14:36) VWAP 至下一交易日全天最高价的复权收益排名"
    else:
        label_builder = get_label_builder(label_set, label_version)
        label_column = label_builder.label_column
        lookahead = label_builder.lookahead
        maturity_time = time(15, 0)
        target_description = f"当日至第 {lookahead} 个后续交易日的复权收盘收益的横截面百分位排名"
    dataset = FeatureLabelConfig(
        feature_set=feature.feature_set, feature_version=feature.feature_version,
        feature_columns=feature.feature_columns,
        label_set=label_set, label_version=label_version, label_column=label_column,
    )
    configuration = ModelConfig(
        group=preset.group, model_params=preset.model_params,
        preprocessing=preset.preprocessing, dataset=dataset, sample_weight=preset.sample_weight,
    )
    experiment_name = require_new_experiment(
        path_manager=path_manager, kind="training", start_date=submission.start,
        end_date=submission.end, experiment_id=experiment_id,
    )
    start_log = build_training_start_log(submission=submission, configuration=configuration, target_description=target_description)
    logs.info(f"▶️ 训练任务; experiment={experiment_name}\n{start_log}")
    access = Access(pm=path_manager, processed_version=PROCESSED_VERSION)
    train_dates = tuple(access.trade_dates(
        start_date=submission.train_range.start, end_date=submission.train_range.end,
    ))
    if not train_dates:
        raise ValueError("train_range contains no open trading sessions")
    eval_dates: tuple[str, ...] = ()
    if submission.eval_range is not None:
        eval_dates = tuple(access.trade_dates(
            start_date=submission.eval_range.start, end_date=submission.eval_range.end,
        ))
        if not eval_dates:
            raise ValueError("eval_range contains no open trading sessions")
        if train_dates[-1] >= eval_dates[0]:
            raise ValueError("train_range samples must precede eval_range samples")

    if eval_dates and submission.eval_gap_sessions:
        between = access.trade_dates(start_date=train_dates[-1], end_date=eval_dates[0])
        if len(between) - 2 < submission.eval_gap_sessions:
            raise ValueError("eval_range does not satisfy eval_gap_sessions")

    def label_maturity(day: str) -> int:
        for _ in range(lookahead):
            day = access.next_trade_date(trade_date=day)
        return DateTimeUtils.local_time_to_utc_epoch_us(maturity_time, date.fromisoformat(day))

    if eval_dates and label_maturity(train_dates[-1]) > DateTimeUtils.local_time_to_utc_epoch_us(
        decision_time, date.fromisoformat(eval_dates[0]),
    ):
        raise ValueError(
            f"train_range last sample {train_dates[-1]} has a label that matures "
            f"after the first eval_range prediction on {eval_dates[0]}"
        )
    validation_dates: tuple[str, ...] = ()
    purged_dates: tuple[str, ...] = ()
    if submission.validation_sessions:
        if submission.validation_sessions >= len(train_dates):
            raise ValueError("validation_sessions must leave training sessions in train_range")
        validation_dates = train_dates[-submission.validation_sessions:]
        validation_start = DateTimeUtils.local_time_to_utc_epoch_us(decision_time, date.fromisoformat(validation_dates[0]))
        candidate_dates = train_dates[:-submission.validation_sessions]
        purged_dates = tuple(day for day in candidate_dates if label_maturity(day) > validation_start)
        train_dates = tuple(day for day in candidate_dates if day not in purged_dates)
        if not train_dates:
            raise ValueError("validation split leaves no mature training sessions")
    label_maturity_ts_us = label_maturity(train_dates[-1])
    logs.info(
        f"✅ 训练划分; experiment={experiment_name}\n"
        f"拟合 {len(train_dates)} 日；内部验证 {len(validation_dates)} 日；"
        f"标签未成熟隔离 {len(purged_dates)} 日；最终评估 {len(eval_dates)} 日。"
    )
    model_ref = path_manager.experiment_training_model(experiment_name=experiment_name)
    evaluations = tuple(
        EvaluationDay(
            trade_date=day,
            decision_ts_us=DateTimeUtils.local_time_to_utc_epoch_us(
                decision_time, date.fromisoformat(day),
            ),
            prediction_ref=model_ref.parent / "predictions" / f"{day}.parquet",
            stage="validation" if day in validation_dates else "evaluation",
        )
        for day in (*validation_dates, *eval_dates)
    )
    window = TrainingWindow(train_dates=train_dates, model_ref=model_ref, evaluations=evaluations)
    steps: tuple[PipelineStep[TrainingContext], ...] = (
        DatasetBuildStep(pm=path_manager, dataset_cfg=dataset, clock=monotonic, sample_weight=preset.sample_weight),
        PreprocessStep(preset.preprocessing),
        ModelTrainStep(trainer=trainer, model_params=preset.model_params, dataset_cfg=dataset),
        ArtifactPersistStep(),
    )
    if evaluations:
        steps += (
            PredictionStep(pm=path_manager, clock=monotonic), ICEvaluateStep(pm=path_manager, clock=monotonic),
            MetricsPersistStep(pm=path_manager, experiment_name=experiment_name),
        )
        if eval_dates and feature.feature_set != "previous_day_daily_l2":
            steps += (ReportStep(pm=path_manager, experiment_name=experiment_name),)
    record = TrainingRunRecord(
        request=submission.model_dump(mode="json"), train_dates=train_dates,
        eval_dates=eval_dates, model_ref=model_ref, configuration=configuration,
        label_maturity_ts_us=label_maturity_ts_us,
        validation_dates=validation_dates, purged_dates=purged_dates,
    )
    schedule_ref = path_manager.experiment_training_schedule(experiment_name=experiment_name)
    FileSystem.write_bytes_atomic(
        schedule_ref,
        record.model_dump_json(indent=2).encode("utf-8"),
    )
    TrainingPipeline(
        window=window, steps=steps, instrumentation=Instrumentation(experiment_name, descriptions={
            "DatasetBuildStep": "数据准备", "PreprocessStep": "特征预处理", "ModelTrainStep": "模型拟合",
            "ArtifactPersistStep": "模型写入", "PredictionStep": "批量预测", "ICEvaluateStep": "IC 评价",
            "MetricsPersistStep": "IC 指标写入", "ReportStep": "诊断计算与报告输出",
        }),
    ).run()
    if evaluations:
        if feature.feature_set == "previous_day_daily_l2":
            save_top_selection_evaluation(
                pm=path_manager, experiment_name=experiment_name,
                periods={day.trade_date: day.stage for day in evaluations},
            )
        outputs = "模型、评价与报告均已写入。" if eval_dates or feature.feature_set == "previous_day_daily_l2" else "模型与内部验证指标均已写入；本次未请求最终评估。"
        logs.info(f"✅ 训练任务; experiment={experiment_name}\n{outputs}")
    else:
        logs.info(
            f"✅ 训练任务; experiment={experiment_name}\n本次未请求评价；未生成 IC 指标或 HTML 报告。\n"
            f"模型：{model_ref}\n运行配置：{schedule_ref}"
        )
