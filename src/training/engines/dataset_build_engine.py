# filepath: src/training/engines/dataset_build_engine.py
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
import pandas as pd

from src.utils.price_utils import apply_asof_price_adjustment


class DatasetBuildEngine:
    """Build aligned feature and label arrays.

    Responsibility:
    - Pure dataset construction logic
    - NO IO
    - NO PathManager
    - NO Context

    Example:
        engine = DatasetBuildEngine()
        X, y = engine.build_one_day(
            feature_frame=pd.DataFrame({"factor": [1.0]}),
            label_frame=pd.DataFrame({"target": [0.2]}),
            feature_columns=("factor",),
            label_column="target",
            drop_na=True,
        )
    """

    # ==============================================================
    # Public
    # ==============================================================
    def build_one_day(
        self,
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
        """Build one train/eval dataset from already loaded formal inputs.

        Example:
            X, y = engine.build_one_day(
                feature_frame=pd.DataFrame({"factor": [1.0]}),
                label_frame=pd.DataFrame({"target": [0.2]}),
                feature_columns=("factor",),
                label_column="target",
                drop_na=True,
            )
        """
        feat_df = self._apply_adjustment(
            feat_df=feature_frame,
            adjustment=adjustment,
            adjustment_refdata_frame=adjustment_refdata_frame,
            asof_date=asof_date,
        )

        if feat_df.empty:
            raise RuntimeError("[DatasetBuildEngine] feature_frame is empty")

        X = (
            feat_df.loc[:, list(feature_columns)].copy()
            if feature_columns
            else feat_df.copy()
        )
        y = label_frame[label_column].copy()

        metadata_columns = {"trade_date", "ts_us"} & set(X.columns)
        if metadata_columns:
            raise RuntimeError(
                "[DatasetBuildEngine] metadata leaked into feature space: "
                f"{sorted(metadata_columns)}"
            )

        # replace na
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
            raise RuntimeError("[DatasetBuildEngine] X / y index mismatch")

        return X, y

    # ==============================================================
    # Internal
    # ==============================================================
    def _apply_adjustment(
        self,
        *,
        feat_df: pd.DataFrame,
        adjustment: Literal["raw", "qfq", "hfq"],
        adjustment_refdata_frame: pd.DataFrame | None,
        asof_date: str,
    ) -> pd.DataFrame:
        if adjustment not in ["raw", "qfq", "hfq"]:
            raise ValueError(
                f"[DatasetBuildEngine] unsupported adjustment: {adjustment}"
            )

        if adjustment == "raw":
            return feat_df

        if adjustment_refdata_frame is None or adjustment_refdata_frame.empty:
            raise RuntimeError(
                "[DatasetBuildEngine] adjustment_refdata_frame is required"
            )

        out = feat_df.merge(
            adjustment_refdata_frame[["symbol", "trade_date", "adj_factor"]],
            on=["symbol", "trade_date"],
            how="left",
        )
        if out["adj_factor"].isna().any():
            raise RuntimeError(
                "[DatasetBuildEngine] adjustment_refdata missing adj_factor "
                "for feature rows"
            )

        adjusted = apply_asof_price_adjustment(
            out,
            adjustment=adjustment,
            asof_date=asof_date,
        )
        return adjusted.drop(columns=["adj_factor"])
