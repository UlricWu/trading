# filepath: tests/training/engines/test_rank_ic.py
"""Rank IC calculation and summary tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import ConstantInputWarning

from src.training.engines.preprocessing import FittedPreprocessor
from src.training.engines.rank_ic import (
    build_rank_ic_rows,
    evaluate_rank_ic,
    summarize_rank_ic,
)
from src.training.inference_model import InferenceModel


class _FirstColumnModel:
    def predict(self, values: np.ndarray) -> np.ndarray:
        return values[:, 0]


def test_evaluate_rank_ic_uses_aligned_model_predictions() -> None:
    model = InferenceModel(
        model=_FirstColumnModel(),
        preprocess=FittedPreprocessor(
            feature_names=("factor",),
            missing_method="drop",
        ),
        feature_set="daily",
        feature_version="v1",
    )
    rank_ic = evaluate_rank_ic(
        model=model,
        eval_X=pd.DataFrame({"factor": [1.0, np.nan, 3.0]}),
        eval_y=pd.Series([10.0, 200.0, 30.0]),
    )

    assert rank_ic == pytest.approx(1.0)


def test_evaluate_rank_ic_fails_when_drop_retains_too_few_rows() -> None:
    model = InferenceModel(
        model=_FirstColumnModel(),
        preprocess=FittedPreprocessor(
            feature_names=("factor",),
            missing_method="drop",
        ),
        feature_set="daily",
        feature_version="v1",
    )

    with pytest.raises(RuntimeError, match="at least two retained"):
        evaluate_rank_ic(
            model=model,
            eval_X=pd.DataFrame({"factor": [np.nan, 1.0]}),
            eval_y=pd.Series([10.0, 20.0]),
        )


def test_evaluate_rank_ic_rejects_a_nonfinite_correlation() -> None:
    model = InferenceModel(
        model=_FirstColumnModel(),
        preprocess=FittedPreprocessor(
            feature_names=("factor",),
            missing_method="constant",
            fill_values=(0.0,),
        ),
        feature_set="daily",
        feature_version="v1",
    )

    with (
        pytest.warns(ConstantInputWarning),
        pytest.raises(RuntimeError, match="Rank IC must be finite"),
    ):
        evaluate_rank_ic(
            model=model,
            eval_X=pd.DataFrame({"factor": [1.0, 1.0]}),
            eval_y=pd.Series([10.0, 20.0]),
        )


def test_rank_ic_rows_and_summary_are_ordered_by_evaluation_date() -> None:
    rows = build_rank_ic_rows(
        {
            "ic@2026-07-22": 0.3,
            "ic@2026-07-20": 0.1,
            "ic@2026-07-21": 0.2,
        }
    )
    summary = summarize_rank_ic(rows)

    assert [row.eval_date for row in rows] == [
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
    ]
    assert summary.observations == 3
    assert summary.mean_ic == pytest.approx(0.2)
    assert summary.min_ic_date == "2026-07-20"
    assert summary.max_ic_date == "2026-07-22"
