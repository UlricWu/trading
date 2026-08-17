# filepath: src/training/engines/dataset.py
"""Construct aligned training datasets from already loaded formal inputs."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from src.utils import table_ops


def build_daily_training_dataset(
    *,
    feature_frame: pd.DataFrame,
    label_frame: pd.DataFrame,
    feature_columns: Sequence[str],
    label_column: str,
) -> tuple[pd.DataFrame, pd.Series]:
    """Build one daily feature/label dataset from formal input tables.

    Example:
        X, y = build_daily_training_dataset(
            feature_frame=pd.DataFrame({"factor": [1.0]}),
            label_frame=pd.DataFrame({"target": [0.2]}),
            feature_columns=("factor",),
            label_column="target",
        )
    """
    selected_columns = tuple(feature_columns)
    table_ops.require_nonempty(feature_frame, who="training dataset feature_frame")
    table_ops.require_columns(
        feature_frame,
        selected_columns,
        who="training dataset feature_frame",
    )
    table_ops.require_columns(
        label_frame,
        (label_column,),
        who="training dataset label_frame",
    )
    if not feature_frame.index.equals(label_frame.index):
        raise RuntimeError("training dataset feature / label index mismatch")

    metadata_columns = {"trade_date", "ts_us"} & set(selected_columns)
    if metadata_columns:
        raise RuntimeError(
            "training dataset metadata leaked into feature space: "
            f"{sorted(metadata_columns)}"
        )

    X = feature_frame.loc[:, list(selected_columns)].astype(float).copy()
    y = label_frame[label_column].astype(float).copy()
    if np.isinf(X.to_numpy(dtype=float, copy=False)).any():
        raise ValueError("training feature values must not contain infinity")
    if np.isinf(y.to_numpy(dtype=float, copy=False)).any():
        raise ValueError("training label values must not contain infinity")

    labeled_rows = ~y.isna()
    X = X.loc[labeled_rows].copy()
    y = y.loc[labeled_rows].copy()
    if not X.index.equals(y.index):
        raise RuntimeError("training dataset X / y index mismatch")
    return X, y
