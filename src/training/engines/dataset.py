# filepath: src/training/engines/dataset.py
"""Construct aligned training datasets from already loaded formal inputs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
import pandas as pd

from src.utils import table_ops
from src.utils.price_utils import apply_asof_price_adjustment


def build_daily_training_dataset(
    *,
    feature_frame: pd.DataFrame,
    label_frame: pd.DataFrame,
    feature_columns: Sequence[str],
    label_column: str,
    drop_na: bool,
    adjustment: Literal["raw", "qfq", "hfq"] = "raw",
    adjustment_refdata_frame: pd.DataFrame | None = None,
    asof_date: str = "",
) -> tuple[pd.DataFrame, pd.Series]:
    """Build one daily feature/label dataset from formal input tables.

    Example:
        X, y = build_daily_training_dataset(
            feature_frame=pd.DataFrame({"factor": [1.0]}),
            label_frame=pd.DataFrame({"target": [0.2]}),
            feature_columns=("factor",),
            label_column="target",
            drop_na=True,
        )
    """
    adjusted_features = _apply_price_adjustment(
        feature_frame=feature_frame,
        adjustment=adjustment,
        adjustment_refdata_frame=adjustment_refdata_frame,
        asof_date=asof_date,
    )
    table_ops.require_nonempty(
        adjusted_features,
        who="training dataset feature_frame",
    )

    X = (
        adjusted_features.loc[:, list(feature_columns)].copy()
        if feature_columns
        else adjusted_features.copy()
    )
    y = label_frame[label_column].copy()
    metadata_columns = {"trade_date", "ts_us"} & set(X.columns)
    if metadata_columns:
        raise RuntimeError(
            "training dataset metadata leaked into feature space: "
            f"{sorted(metadata_columns)}"
        )

    X.replace([np.inf, -np.inf], np.nan, inplace=True)
    y.replace([np.inf, -np.inf], np.nan, inplace=True)
    if drop_na:
        valid = X.notna().all(axis=1) & y.notna()
        X = X.loc[valid]
        y = y.loc[valid]

    if X.empty:
        return pd.DataFrame(), pd.Series(dtype=float)

    y = y.loc[X.index]
    if not X.index.equals(y.index):
        raise RuntimeError("training dataset X / y index mismatch")
    return X, y


def _apply_price_adjustment(
    *,
    feature_frame: pd.DataFrame,
    adjustment: Literal["raw", "qfq", "hfq"],
    adjustment_refdata_frame: pd.DataFrame | None,
    asof_date: str,
) -> pd.DataFrame:
    if adjustment not in {"raw", "qfq", "hfq"}:
        raise ValueError(f"unsupported training dataset adjustment: {adjustment}")
    if adjustment == "raw":
        return feature_frame
    if adjustment_refdata_frame is None:
        raise RuntimeError("adjustment_refdata_frame is required")
    table_ops.require_nonempty(
        adjustment_refdata_frame,
        who="training dataset adjustment_refdata_frame",
    )

    adjusted_features = feature_frame.merge(
        adjustment_refdata_frame[["symbol", "trade_date", "adj_factor"]],
        on=["symbol", "trade_date"],
        how="left",
    )
    table_ops.require_non_null(
        adjusted_features,
        ("adj_factor",),
        who="training dataset adjusted feature rows",
    )
    adjusted_features = apply_asof_price_adjustment(
        adjusted_features,
        adjustment=adjustment,
        asof_date=asof_date,
    )
    return adjusted_features.drop(columns=["adj_factor"])
