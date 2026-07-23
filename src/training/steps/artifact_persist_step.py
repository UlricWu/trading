# filepath: src/training/steps/artifact_persist_step.py
from __future__ import annotations

import json
import joblib

from src import logs
from src.config.model_config import FeatureLabelConfig
from src.observability.log_format import format_log_json
from src.pipeline.artifact import ModelArtifact
from src.pipeline.step import PipelineStep
from src.training.context import TrainingContext
from src.utils.datetime_utils import DateTimeUtils


class ArtifactPersistStep(PipelineStep[TrainingContext]):
    """
    Persist training outputs under the formal experiment training directory.

    This step writes `model.pkl`, `preprocess.pkl`, `params.json`, and
    `metrics.json` into one experiment training directory.
    """

    stage = "training_finalize"

    def __init__(
        self,
        *,
        experiment_id: str,
        model_group: str,
        dataset_cfg: FeatureLabelConfig,
        label_lookahead: int,
    ) -> None:
        super().__init__()
        self.experiment_id = experiment_id
        self.model_group = model_group
        self.dataset_cfg = dataset_cfg
        if isinstance(label_lookahead, bool) or not isinstance(label_lookahead, int):
            raise TypeError("label_lookahead must be an int")
        if label_lookahead < 0:
            raise ValueError("label_lookahead must be non-negative")
        self.label_lookahead = label_lookahead

    def run(self, ctx: TrainingContext) -> TrainingContext:
        state = ctx.model_state
        if state is None:
            raise RuntimeError("[ArtifactPersistStep] model_state is missing")

        preprocess = ctx.preprocess_artifact
        if preprocess is None:
            raise RuntimeError("[ArtifactPersistStep] preprocess_artifact is missing")
        if not preprocess.feature_columns:
            raise RuntimeError("[ArtifactPersistStep] feature_columns is empty")
        if state.asof_day != ctx.train_end_date:
            raise RuntimeError(
                "[ArtifactPersistStep] model_state.asof_day does not match "
                "ctx.train_end_date"
            )

        artifact_dir = ctx.pm.experiment_training_dir(
            experiment_name=ctx.experiment_name
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        model_path = ctx.pm.experiment_training_model(
            experiment_name=ctx.experiment_name
        )
        preprocess_path = ctx.pm.experiment_training_preprocess(
            experiment_name=ctx.experiment_name
        )
        params_path = ctx.pm.experiment_training_params(
            experiment_name=ctx.experiment_name
        )
        metrics_path = ctx.pm.experiment_training_metrics(
            experiment_name=ctx.experiment_name
        )
        joblib.dump(state.model, model_path)
        joblib.dump(preprocess, preprocess_path)

        logs.info(
            f"[ARTIFACT] type=model path={artifact_dir} "
            f"model_group={self.model_group} asof_day={state.asof_day}"
        )

        created_at = DateTimeUtils.now()
        adjustment = self.dataset_cfg.adjustment
        params = {
            "experiment_id": self.experiment_id,
            "experiment_name": ctx.experiment_name,
            "model_group": self.model_group,
            "asof_day": state.asof_day,
            "created_at": created_at.isoformat(),
            "feature_names": list(preprocess.feature_columns),
            "feature_set": self.dataset_cfg.feature_set,
            "feature_version": self.dataset_cfg.feature_version,
            "label_set": self.dataset_cfg.label_set,
            "label_version": self.dataset_cfg.label_version,
            "label_column": self.dataset_cfg.label_column,
            "label_lookahead": self.label_lookahead,
            "price_adjustment": adjustment.method,
            "adjustment_refdata": {
                "dataset_name": adjustment.dataset_name,
                "version": adjustment.version,
            },
        }
        params_path.write_text(
            json.dumps(params, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        metrics_path.write_text(
            json.dumps(dict(ctx.metrics), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        ctx.model_artifact = ModelArtifact(
            model_path=model_path,
            preprocess_path=preprocess_path,
            model_group=self.model_group,
            experiment_id=self.experiment_id,
            asof_day=state.asof_day,
            created_at=created_at,
            metrics=dict(ctx.metrics),
            feature_names=list(preprocess.feature_columns),
            feature_set=self.dataset_cfg.feature_set,
            feature_version=self.dataset_cfg.feature_version,
            price_adjustment=adjustment.method,
            label_lookahead=self.label_lookahead,
        )

        logs.info(
            "[ArtifactPersistStep] saved\n"
            f"{format_log_json('model_artifact', ctx.model_artifact)}"
        )
        return ctx
