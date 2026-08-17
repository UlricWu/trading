# filepath: tests/training/engines/test_report.py
"""Pure training-report rendering tests."""

from __future__ import annotations

from src.training.artifact import TrainingParams
from src.training.engines.report import build_training_report


def test_build_training_report_renders_persisted_identity_and_rank_ic() -> None:
    report = build_training_report(
        experiment_name="training_2026-07-01_2026-07-20_run-1",
        params=TrainingParams(
            experiment_id="run-1",
            model_group="sgd_regression",
            asof_day="2026-07-19",
            feature_set="daily",
            feature_version="v1",
            feature_names=("factor",),
            label_set="rank",
            label_version="v1",
            label_column="target",
            label_lookahead=1,
        ),
        metrics={"ic@2026-07-20": 0.1},
    )

    assert report.summary.observations == 1
    assert "training_2026-07-01_2026-07-20_run-1" in report.html
    assert "0.100000" in report.html
    assert "created_at" not in report.html
    assert "price_adjustment" not in report.html
