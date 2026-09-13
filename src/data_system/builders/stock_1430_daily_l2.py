# filepath: src/data_system/builders/stock_1430_daily_l2.py
"""Fuse previous-session daily ranks into the fixed H03 Feature universe."""

from __future__ import annotations

from datetime import date

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from src.data_system.builders.stock_1430 import (
    STOCK_1430_DECISION_TIME,
    STOCK_1430_FEATURE_SCHEMA,
    require_stock_1430_feature_keys,
)
from src.utils import table_ops
from src.utils.datetime_utils import DateTimeUtils

__all__ = (
    "STOCK_1430_DAILY_L2_SCHEMA",
    "STOCK_1430_DAILY_SOURCE_COLUMNS",
    "build_stock_1430_daily_l2_features",
)

STOCK_1430_DAILY_SOURCE_COLUMNS = (
    "f_d_close_return_1d",
    "f_d_open_gap_1d",
    "f_d_log_amount",
    "f_d_max_drawdown_20d_asof_tminus1",
    "f_d_close_volatility_60d_asof_tminus1",
    "f_d_close_return_5d_asof_tminus1",
    "f_d_turnover_rate_mean_20d_asof_tminus1",
)
STOCK_1430_DAILY_L2_SCHEMA = pa.schema(
    [
        *STOCK_1430_FEATURE_SCHEMA,
        *[
            pa.field(f"{column}_rank", pa.float64())
            for column in STOCK_1430_DAILY_SOURCE_COLUMNS
        ],
    ]
)


def build_stock_1430_daily_l2_features(
    l2_features: pa.Table,
    daily_features: pa.Table,
    *,
    trade_date: date,
    previous_trade_date: date,
) -> pa.Table:
    """Append seven daily-P ranks while preserving every H03-T array and row.

    The caller resolves P/T with the formal calendar and reads committed
    Feature payloads. This boundary validates their consumed schema and keys;
    the formulas and null rules belong to
    ``docs/data/stock_1430_daily_l2_contract.md``.

    Example:
        features = build_stock_1430_daily_l2_features(
            l2_features_t,
            daily_features_p,
            trade_date=date(2026, 5, 6),
            previous_trade_date=date(2026, 4, 30),
        )
    """
    if not l2_features.schema.equals(STOCK_1430_FEATURE_SCHEMA, check_metadata=False):
        raise ValueError("L2 Feature schema must match l2_stock_1430/v1")
    require_stock_1430_feature_keys(
        l2_features,
        trade_date=trade_date.isoformat(),
        decision_ts_utc=DateTimeUtils.local_time_to_utc_epoch_us(
            STOCK_1430_DECISION_TIME, trade_date
        ),
    )

    daily_columns = ("symbol", "trade_date", *STOCK_1430_DAILY_SOURCE_COLUMNS)
    table_ops.require_columns(daily_features, daily_columns, who="daily P Feature")
    table_ops.require_nonempty(daily_features, who="daily P Feature")
    table_ops.require_nonempty_strings(
        daily_features, ("symbol", "trade_date"), who="daily P Feature"
    )
    table_ops.require_unique(daily_features, ("symbol",), who="daily P Feature")
    if (
        pc.all(
            pc.equal(
                daily_features.column("trade_date"), previous_trade_date.isoformat()
            )
        ).as_py()
        is not True
    ):
        raise ValueError("daily Feature trade_date does not match P partition")
    invalid_types = [
        column
        for column in STOCK_1430_DAILY_SOURCE_COLUMNS
        if not daily_features.schema.field(column).type.equals(pa.float64())
    ]
    if invalid_types:
        raise ValueError(f"daily P Feature columns must be float64: {invalid_types!r}")

    daily_by_symbol = (
        daily_features.select(("symbol", *STOCK_1430_DAILY_SOURCE_COLUMNS))
        .to_pandas()
        .set_index("symbol")
    )
    infinite_columns = [
        column
        for column in STOCK_1430_DAILY_SOURCE_COLUMNS
        if np.isinf(daily_by_symbol[column]).any()
    ]
    if infinite_columns:
        raise ValueError(f"daily P Feature contains infinity: {infinite_columns!r}")
    aligned = daily_by_symbol.reindex(l2_features.column("symbol").to_pylist())
    ranks = aligned.rank(method="average", ascending=True, pct=True)
    return pa.Table.from_arrays(
        [
            *l2_features.columns,
            *[
                pa.array(ranks[column], type=pa.float64(), from_pandas=True)
                for column in STOCK_1430_DAILY_SOURCE_COLUMNS
            ],
        ],
        schema=STOCK_1430_DAILY_L2_SCHEMA,
    )
