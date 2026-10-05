# filepath: src/access/feature_label.py
"""Read committed feature/label partitions without changing their sample rows."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pyarrow.parquet as pq

from src.access import meta
from src.utils import table_ops
from src.utils.path import PathManager


def read_features(
    *, pm: PathManager, feature_set: str, version: str,
    trade_date: str, feature_names: Sequence[str],
) -> pd.DataFrame:
    """Return complete sample keys followed by selected, ordered raw features.

    Daily partitions have two keys; decision partitions additionally carry
    decision_ts_utc. Missing feature values remain missing, without filtering rows.

    Example:
        frame = read_features(
            pm=path_manager, feature_set="tushare_daily_basic", version="v1",
            trade_date="2026-07-20", feature_names=("f_d_close_return_1d",),
        )
    """
    paths = pm.feature_object(feature_set=feature_set, version=version, trade_date=trade_date)
    record = meta.require(pm=pm, meta_path=paths.meta_path,
                          expected_payload_path=paths.payload_path)
    with pq.ParquetFile(record.payload_path) as source:
        keys = ("symbol", "trade_date")
        if (feature_set, version) == ("l2_stock_1430", "v1") or "decision_ts_utc" in source.schema_arrow.names:
            keys += ("decision_ts_utc",)
        if set(feature_names) & {*keys, "ts_us"}:
            raise ValueError("sample keys must not be selected as features")
        frame = source.read(columns=[*keys, *feature_names]).to_pandas()
    _require_keys(frame, keys, trade_date)
    return frame


def read_labels(
    *, pm: PathManager, label_set: str, version: str,
    trade_date: str, label_column: str,
) -> pd.DataFrame:
    """Return complete sample keys and labels, retaining unknown-label rows.

    Example:
        frame = read_labels(
            pm=path_manager, label_set="daily_close_return_rank_d1", version="v1",
            trade_date="2026-07-20", label_column="y_rank_return",
        )
    """
    paths = pm.label_object(label_set=label_set, version=version, trade_date=trade_date)
    record = meta.require(pm=pm, meta_path=paths.meta_path,
                          expected_payload_path=paths.payload_path)
    with pq.ParquetFile(record.payload_path) as source:
        keys = ("symbol", "trade_date")
        if (label_set, version) == ("l2_stock_1430_t1_vwap_rank", "v1") or "decision_ts_utc" in source.schema_arrow.names:
            keys += ("decision_ts_utc",)
        frame = source.read(columns=[*keys, label_column]).to_pandas()
    _require_keys(frame, keys, trade_date)
    return frame


def _require_keys(frame: pd.DataFrame, keys: tuple[str, ...], trade_date: str) -> None:
    table_ops.require_nonempty_strings(frame, ("symbol", "trade_date"), who="feature/label")
    table_ops.require_unique(frame, keys, who="feature/label")
    if not frame["trade_date"].eq(trade_date).all():
        raise ValueError("feature/label partition date mismatch")
    if "decision_ts_utc" in keys:
        times = frame["decision_ts_utc"]
        if times.isna().any() or not pd.api.types.is_integer_dtype(times.dtype):
            raise ValueError("decision_ts_utc must contain integer UTC epoch microseconds")
