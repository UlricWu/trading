# filepath: src/workflows/offline_training.py
"""Compose one selected model fit and optional fixed-model daily evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, time
from time import monotonic

from src import logs
from src.access import Access
from src.config.model_config import FeatureGroupConfig, FeatureLabelConfig, ModelConfig, ModelPreset
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
    """Resolve the user's choices, fit once, then evaluate the same saved model.

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
    else:
        get_feature_builder(feature.feature_set, feature.feature_version)
        decision_time = time(15, 0)
    if (label_set, label_version) == ("l2_stock_1430_t1_vwap_rank", "v1"):
        label_column = "y_rank_return"
        lookahead = 1
        maturity_time = time(14, 36)
        target_description = "当日至下一交易日 [14:31, 14:36) VWAP 窗口的复权收益横截面百分位排名"
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
        preprocessing=preset.preprocessing, dataset=dataset,
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

    mature_date = train_dates[-1]
    for _ in range(lookahead):
        mature_date = access.next_trade_date(trade_date=mature_date)
    label_maturity_ts_us = DateTimeUtils.local_time_to_utc_epoch_us(
        maturity_time, date.fromisoformat(mature_date),
    )
    model_ref = path_manager.experiment_training_model(experiment_name=experiment_name)
    evaluations = tuple(
        EvaluationDay(
            trade_date=day,
            decision_ts_us=DateTimeUtils.local_time_to_utc_epoch_us(
                decision_time, date.fromisoformat(day),
            ),
            prediction_ref=model_ref.parent / "predictions" / f"{day}.parquet",
        )
        for day in eval_dates
    )
    if evaluations and label_maturity_ts_us > evaluations[0].decision_ts_us:
        raise ValueError(
            f"train_range last sample {train_dates[-1]} has a label that matures "
            f"after the first eval_range prediction on {eval_dates[0]}"
        )
    window = TrainingWindow(train_dates=train_dates, model_ref=model_ref, evaluations=evaluations)
    steps: tuple[PipelineStep[TrainingContext], ...] = (
        DatasetBuildStep(pm=path_manager, dataset_cfg=dataset, clock=monotonic),
        PreprocessStep(preset.preprocessing),
        ModelTrainStep(trainer=trainer, model_params=preset.model_params, dataset_cfg=dataset),
        ArtifactPersistStep(),
    )
    if evaluations:
        steps += (
            PredictionStep(pm=path_manager, clock=monotonic), ICEvaluateStep(pm=path_manager, clock=monotonic),
            MetricsPersistStep(pm=path_manager, experiment_name=experiment_name),
            ReportStep(pm=path_manager, experiment_name=experiment_name),
        )
    record = TrainingRunRecord(
        request=submission.model_dump(mode="json"), train_dates=train_dates,
        eval_dates=eval_dates, model_ref=model_ref, configuration=configuration,
        label_maturity_ts_us=label_maturity_ts_us,
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
        logs.info(f"✅ 训练任务; experiment={experiment_name}\n模型、评价与报告均已写入。")
    else:
        logs.info(
            f"✅ 训练任务; experiment={experiment_name}\n本次未请求评价；未生成 IC 指标或 HTML 报告。\n"
            f"模型：{model_ref}\n运行配置：{schedule_ref}"
        )
