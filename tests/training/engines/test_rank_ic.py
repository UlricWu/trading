# filepath: tests/training/engines/test_rank_ic.py
"""Rank IC calculation and summary tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.training.engines.rank_ic import (
    build_rank_ic_rows,
    evaluate_rank_ic,
    summarize_rank_ic,
)


class _FirstColumnModel:
    def predict(self, values: np.ndarray) -> np.ndarray:
        return values[:, 0]


def test_evaluate_rank_ic_uses_aligned_model_predictions() -> None:
    rank_ic = evaluate_rank_ic(
        model=_FirstColumnModel(),
        eval_X=pd.DataFrame({"factor": [1.0, 2.0, 3.0]}),
        eval_y=pd.Series([10.0, 20.0, 30.0]),
    )

    assert rank_ic == pytest.approx(1.0)


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
