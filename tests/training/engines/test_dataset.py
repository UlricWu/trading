# filepath: tests/training/engines/test_dataset.py
"""Pure training-dataset construction tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.training.engines.dataset import build_daily_training_dataset


def test_build_daily_training_dataset_keeps_feature_nan_for_preprocessing() -> None:
    features = pd.DataFrame(
        {
            "symbol": ["600000", "600001", "600002"],
            "trade_date": ["2026-07-20"] * 3,
            "factor": [1.0, np.nan, 3.0],
            "unused": [10.0, 20.0, 30.0],
        },
    )
    labels = pd.DataFrame(
        {
            "symbol": ["600000", "600001", "600002"],
            "trade_date": ["2026-07-20"] * 3,
            "target": [0.1, 0.2, np.nan],
        },
    )

    X, y = build_daily_training_dataset(
        feature_frame=features,
        label_frame=labels,
        feature_columns=("factor",),
        label_column="target",
    )

    assert X.index.tolist() == [0, 1]
    assert X.loc[0, "factor"] == 1.0
    assert np.isnan(X.loc[1, "factor"])
    assert y.tolist() == [0.1, 0.2]


@pytest.mark.parametrize(
    ("features", "labels", "message"),
    [
        ([np.inf], [0.1], "feature"),
        ([1.0], [np.inf], "label"),
    ],
)
def test_build_daily_training_dataset_rejects_infinity(
    features: list[float],
    labels: list[float],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_daily_training_dataset(
            feature_frame=pd.DataFrame(
                {
                    "symbol": ["600000"],
                    "trade_date": ["2026-07-20"],
                    "factor": features,
                }
            ),
            label_frame=pd.DataFrame(
                {
                    "symbol": ["600000"],
                    "trade_date": ["2026-07-20"],
                    "target": labels,
                }
            ),
            feature_columns=("factor",),
            label_column="target",
        )


def test_build_daily_training_dataset_rejects_key_mismatch() -> None:
    features = pd.DataFrame(
        {
            "symbol": ["600000"],
            "trade_date": ["2026-07-20"],
            "factor": [1.0],
        }
    )
    labels = pd.DataFrame(
        {
            "symbol": ["600001"],
            "trade_date": ["2026-07-20"],
            "target": [0.1],
        }
    )

    with pytest.raises(RuntimeError, match="key mismatch"):
        build_daily_training_dataset(
            feature_frame=features,
            label_frame=labels,
            feature_columns=("factor",),
            label_column="target",
        )
