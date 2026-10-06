# filepath: src/training/steps/report.py
"""Publish fitted-model diagnostics in the training report and job logs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import numpy as np
from sklearn.linear_model import SGDRegressor

from src import logs
from src.training.artifact import load
from src.training.context import TrainingContext
from src.training.engines.report import build_training_log, build_training_report, summarize_training
from src.training.evaluation import load_evaluation, read_evaluation_labels
from src.training.prediction import read_prediction_record
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


class ReportStep:
    """Report the delivered model using retained fit rows and saved predictions.

    Example:
        step = ReportStep(pm=path_manager, experiment_name="training_run")
        context = step.run(evaluated_context)
    """

    def __init__(
        self, *, pm: PathManager, experiment_name: str,
    ) -> None:
        """Bind the experiment's metrics and report paths.

        Example:
            step = ReportStep(pm=path_manager, experiment_name="training_run")
        """
        self._pm = pm
        self._experiment_name = experiment_name

    def run(self, context: TrainingContext) -> TrainingContext:
        """Write HTML and completion logs from one common diagnostic summary.

        Example:
            context = step.run(evaluated_context)
        """
        if context.train_X is None or context.train_y is None:
            raise RuntimeError("ReportStep requires the retained preprocessed training rows")
        logs.info(f"▶️ 诊断计算与报告输出; experiment={self._experiment_name}")
        model_ref = context.window.model_ref
        model = load(model_ref)
        with model_ref.open("rb") as source:
            model_digest = hashlib.file_digest(source, "sha256").hexdigest()
        metrics_ref = self._pm.experiment_training_metrics(experiment_name=self._experiment_name)
        evaluation = load_evaluation(metrics_ref)
        prediction_arrays: list[np.ndarray] = []
        label_arrays: list[np.ndarray] = []
        for observation in evaluation.observations:
            try:
                if observation.prediction_ref is None:
                    raise ValueError("training report requires persisted prediction references")
                inputs, rows = read_prediction_record(Path(observation.prediction_ref))
                if (
                    inputs.model_ref != model_ref or observation.model_ref != str(model_ref)
                    or inputs.model_sha256 != model_digest
                    or inputs.decision_ts_us != observation.decision_ts_us
                ):
                    raise ValueError("training report prediction inputs disagree with the evaluated model or date")
                labels = read_evaluation_labels(pm=self._pm, inputs=inputs, model=model, predictions=rows)
                if str(labels.ref) != observation.label_ref or labels.sha256 != observation.label_sha256:
                    raise ValueError("training report label content has changed since IC evaluation")
            except Exception as exc:
                trade_date = DateTimeUtils.to_local(DateTimeUtils.from_utc_epoch_us(observation.decision_ts_us)).date().isoformat()
                exc.add_note(f"诊断材料读取; trade_date={trade_date} prediction_ref={observation.prediction_ref} model_ref={model_ref}")
                raise
            prediction_arrays.append(rows["prediction"].to_numpy(dtype=np.float64))
            label_arrays.append(labels.values)
        # train_X already contains the fitted transform; applying model.predict would transform twice.
        train_predictions = np.asarray(model.model.predict(context.train_X.to_numpy(dtype=np.float64)), dtype=np.float64)
        if not np.isfinite(train_predictions).all():
            raise RuntimeError("training report model predictions must be finite")
        summary = summarize_training(
            train_predictions=train_predictions, train_labels=context.train_y.to_numpy(dtype=np.float64),
            evaluation_predictions=np.concatenate(prediction_arrays), evaluation_labels=np.concatenate(label_arrays),
            evaluation=evaluation,
        )
        actual_iterations = int(cast(SGDRegressor, model.model).n_iter_)
        html = build_training_report(
            experiment_name=self._experiment_name, model_ref=model_ref, model=model,
            evaluation=evaluation, summary=summary, actual_iterations=actual_iterations,
        )
        output = self._pm.experiment_training_report(experiment_name=self._experiment_name)
        FileSystem.write_bytes_atomic(output, html.encode("utf-8"))
        log_summary = build_training_log(
            model=model, evaluation=evaluation, summary=summary, actual_iterations=actual_iterations,
            model_ref=model_ref, metrics_ref=metrics_ref, report_ref=output,
            schedule_ref=self._pm.experiment_training_schedule(experiment_name=self._experiment_name),
        )
        logs.info(f"✅ 训练报告; experiment={self._experiment_name}\n{log_summary}")
        return context
