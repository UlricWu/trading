# filepath: tests/training/engines/test_dataset.py
"""Pure training-dataset construction tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.training.engines.dataset import build_daily_training_dataset


def test_build_daily_training_dataset_aligns_and_drops_invalid_rows() -> None:
    features = pd.DataFrame(
        {"factor": [1.0, np.inf], "unused": [10.0, 20.0]},
        index=["600000", "600001"],
    )
    labels = pd.DataFrame(
        {"target": [0.1, 0.2]},
        index=["600000", "600001"],
    )

    X, y = build_daily_training_dataset(
        feature_frame=features,
        label_frame=labels,
        feature_columns=("factor",),
        label_column="target",
        drop_na=True,
    )

    assert X.to_dict(orient="index") == {"600000": {"factor": 1.0}}
    assert y.to_dict() == {"600000": 0.1}
