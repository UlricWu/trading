# filepath: tests/training/engines/test_dataset.py
"""Pure training-dataset construction tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.training.engines.dataset import build_daily_training_dataset


def test_build_daily_training_dataset_keeps_feature_nan_for_preprocessing() -> None:
    features = pd.DataFrame(
        {"factor": [1.0, np.nan, 3.0], "unused": [10.0, 20.0, 30.0]},
        index=["600000", "600001", "600002"],
    )
    labels = pd.DataFrame(
        {"target": [0.1, 0.2, np.nan]},
        index=["600000", "600001", "600002"],
    )

    X, y = build_daily_training_dataset(
        feature_frame=features,
        label_frame=labels,
        feature_columns=("factor",),
        label_column="target",
    )

    assert X.index.tolist() == ["600000", "600001"]
    assert X.loc["600000", "factor"] == 1.0
    assert np.isnan(X.loc["600001", "factor"])
    assert y.to_dict() == {"600000": 0.1, "600001": 0.2}


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
            feature_frame=pd.DataFrame({"factor": features}),
            label_frame=pd.DataFrame({"target": labels}),
            feature_columns=("factor",),
            label_column="target",
        )
