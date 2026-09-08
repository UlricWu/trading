# filepath: src/training/engines/dataset.py
"""Construct aligned training datasets from already loaded formal inputs."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from src.utils import table_ops

_KEY_COLUMNS = ("symbol", "trade_date")


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
            feature_frame=pd.DataFrame({
                "symbol": ["600000"],
                "trade_date": ["2026-07-20"],
                "factor": [1.0],
            }),
            label_frame=pd.DataFrame({
                "symbol": ["600000"],
                "trade_date": ["2026-07-20"],
                "target": [0.2],
            }),
            feature_columns=("factor",),
            label_column="target",
        )
    """
    selected_columns = tuple(feature_columns)
    table_ops.require_nonempty(feature_frame, who="training dataset feature_frame")
    table_ops.require_columns(
        feature_frame,
        (*_KEY_COLUMNS, *selected_columns),
        who="training dataset feature_frame",
    )
    table_ops.require_columns(
        label_frame,
        (*_KEY_COLUMNS, label_column),
        who="training dataset label_frame",
    )
    feature_keys = feature_frame.loc[:, list(_KEY_COLUMNS)].reset_index(drop=True)
    label_keys = label_frame.loc[:, list(_KEY_COLUMNS)].reset_index(drop=True)
    if (
        len(feature_keys) != len(label_keys)
        or not feature_keys.eq(label_keys).to_numpy(dtype=bool, na_value=False).all()
    ):
        raise RuntimeError("training dataset feature / label key mismatch")

    metadata_columns = {"trade_date", "ts_us"} & set(selected_columns)
    if metadata_columns:
        raise RuntimeError(
            "training dataset metadata leaked into feature space: "
            f"{sorted(metadata_columns)}"
        )

    X = (
        feature_frame.loc[:, list(selected_columns)]
        .astype(float)
        .reset_index(drop=True)
    )
    y = label_frame[label_column].astype(float).reset_index(drop=True)
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
