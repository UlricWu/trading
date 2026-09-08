# filepath: src/data_system/builders/stock_1430.py
"""Build the fixed V1 14:30 Level-2 Feature and T+1 Label tables."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from src.data_system.market_phase import MarketPhase
from src.utils import table_ops
from src.utils.datetime_utils import DateTimeUtils
from src.utils.price_utils import apply_asof_price_adjustment

__all__ = (
    "STOCK_1430_KEY_SCHEMA",
    "build_stock_1430_features",
    "build_stock_1430_labels",
)

STOCK_1430_KEY_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("trade_date", pa.string(), nullable=False),
        pa.field("decision_ts_utc", pa.int64(), nullable=False),
    ]
)
_FEATURE_WINDOWS_MINUTES = (5, 15, 30, 60)
_DECISION_TIME = time(14, 30)
_LABEL_WINDOW_START = time(14, 31)
_LABEL_WINDOW_END = time(14, 36)
_MINUTE_US = 60_000_000
_FEATURE_INPUT_COLUMNS = (
    "symbol",
    "minute_start_ts_utc",
    "high",
    "low",
    "volume_sum",
    "notional_sum",
    "trade_count",
    "tick_signed_volume_sum",
    "tick_signed_notional_sum",
)
_RANK_METRICS = (
    "edge_vwap_return",
    "high_low_range",
    "notional",
    "trade_count",
    "average_trade_notional",
    "tick_signed_volume_ratio",
    "tick_signed_notional_ratio",
)


_FEATURE_SCHEMA = pa.schema(
    [
        *STOCK_1430_KEY_SCHEMA,
        *[
            field
            for window_minutes in _FEATURE_WINDOWS_MINUTES
            for field in (
                *[
                    pa.field(f"f_l2_{metric}_rank_{window_minutes}m", pa.float64())
                    for metric in _RANK_METRICS
                ],
                pa.field(
                    f"f_l2_observed_minute_ratio_{window_minutes}m",
                    pa.float64(),
                    nullable=False,
                ),
            )
        ],
    ]
)
_LABEL_SCHEMA = pa.schema(
    [
        *STOCK_1430_KEY_SCHEMA,
        pa.field("y_rank_return", pa.float64(), nullable=True),
    ]
)


def build_stock_1430_features(
    minutes: pa.Table,
    *,
    trade_date: date,
) -> pa.Table:
    """Build one V1 14:30 cross-sectional Level-2 Feature partition.

    ``minutes`` is the H02 table returned by ``Access.stock_trade_minutes``.
    Access owns persisted schema, date and key validation; H02 owns finite
    minute values. Computation follows the fixed V1 rules in
    ``docs/data/stock_1430_feature_label_contract.md``. The caller supplies the
    resolved session as a ``date``; the returned table has 3 keys and 32 features.

    Example:
        features = build_stock_1430_features(
            minute_facts,
            trade_date=date(2026, 5, 6),
        )
    """
    decision_ts_utc = DateTimeUtils.local_time_to_utc_epoch_us(
        _DECISION_TIME, trade_date
    )
    visible = minutes.filter(
        pc.and_(
            pc.equal(
                minutes.column("phase"),
                pa.scalar(int(MarketPhase.CONTINUOUS), type=pa.int8()),
            ),
            pc.less(
                minutes.column("minute_start_ts_utc"),
                pa.scalar(decision_ts_utc, type=pa.int64()),
            ),
        )
    )
    symbols = sorted(pc.unique(visible.column("symbol")).to_pylist())
    owned_output = pd.DataFrame(
        {
            "symbol": pd.Series(symbols, dtype="string"),
            "trade_date": pd.Series(
                [trade_date.isoformat()] * len(symbols),
                dtype="string",
            ),
            "decision_ts_utc": pd.Series(
                [decision_ts_utc] * len(symbols),
                dtype="int64",
            ),
        }
    )
    window_input = visible.filter(
        pc.greater_equal(
            visible.column("minute_start_ts_utc"),
            decision_ts_utc - max(_FEATURE_WINDOWS_MINUTES) * _MINUTE_US,
        )
    ).select(_FEATURE_INPUT_COLUMNS)
    visible_frame = window_input.drop(
        ["trade_count", "tick_signed_volume_sum"]
    ).to_pandas()

    for window_minutes in _FEATURE_WINDOWS_MINUTES:
        window_start_ts_utc = decision_ts_utc - window_minutes * _MINUTE_US
        window_rows = visible_frame.loc[
            visible_frame["minute_start_ts_utc"].ge(window_start_ts_utc)
        ]
        grouped = window_rows.groupby("symbol", sort=False, observed=True)
        observed_count = grouped["minute_start_ts_utc"].nunique()
        notional_sum = grouped["notional_sum"].sum()
        integer_sums = _sum_window_integers(
            window_input.filter(
                pc.greater_equal(
                    window_input.column("minute_start_ts_utc"), window_start_ts_utc
                )
            ),
            columns=("trade_count", "volume_sum", "tick_signed_volume_sum"),
        )
        signed_notional_sum = grouped["tick_signed_notional_sum"].sum()
        maximum_high = grouped["high"].max()
        minimum_low = grouped["low"].min()

        first_rows = window_rows.loc[
            window_rows["minute_start_ts_utc"].eq(window_start_ts_utc)
        ].set_index("symbol")
        last_rows = window_rows.loc[
            window_rows["minute_start_ts_utc"].eq(decision_ts_utc - _MINUTE_US)
        ].set_index("symbol")
        first_vwap = _positive_ratio(
            first_rows["notional_sum"],
            first_rows["volume_sum"],
        )
        last_vwap = _positive_ratio(
            last_rows["notional_sum"],
            last_rows["volume_sum"],
        )
        raw_metrics = {
            "edge_vwap_return": _positive_ratio(last_vwap, first_vwap) - 1.0,
            "high_low_range": _positive_ratio(maximum_high, minimum_low) - 1.0,
            "notional": notional_sum.where(np.isfinite(notional_sum)),
            "trade_count": integer_sums["trade_count"],
            "average_trade_notional": _positive_ratio(
                notional_sum,
                integer_sums["trade_count"],
            ),
            "tick_signed_volume_ratio": _finite_ratio(
                integer_sums["tick_signed_volume_sum"],
                integer_sums["volume_sum"],
            ),
            "tick_signed_notional_ratio": _finite_ratio(
                signed_notional_sum,
                notional_sum,
            ),
        }
        for metric in _RANK_METRICS:
            raw_values = owned_output["symbol"].map(raw_metrics[metric])
            owned_output.loc[:, f"f_l2_{metric}_rank_{window_minutes}m"] = (
                _percentile_rank(raw_values)
            )
        owned_output.loc[:, f"f_l2_observed_minute_ratio_{window_minutes}m"] = (
            owned_output["symbol"].map(observed_count).fillna(0.0).astype("float64")
            / float(window_minutes)
        )

    return pa.Table.from_pandas(
        owned_output,
        schema=_FEATURE_SCHEMA,
        preserve_index=False,
        safe=True,
    )


def build_stock_1430_labels(
    *,
    feature_keys: pa.Table,
    entry_minutes: pa.Table,
    exit_minutes: pa.Table,
    entry_factors: pd.DataFrame,
    exit_factors: pd.DataFrame,
    trade_date: date,
    next_trade_date: date,
) -> pa.Table:
    """Build one V1 T+1 VWAP rank Label on committed Feature keys.

    Minute and factor tables come from ``Access.stock_trade_minutes`` and
    ``Access.adjustment_factors`` for the respective dates. Access owns their
    persisted identity and the calendar's immediately next session; this builder
    validates committed Feature keys and computes nullable supervision. The
    result reuses those key arrays, followed by ``y_rank_return``.

    Example:
        labels = build_stock_1430_labels(
            feature_keys=features.select(STOCK_1430_KEY_SCHEMA.names),
            entry_minutes=minutes_t,
            exit_minutes=minutes_t1,
            entry_factors=factors_t,
            exit_factors=factors_t1,
            trade_date=date(2026, 5, 6),
            next_trade_date=date(2026, 5, 7),
        )
    """
    expected_decision_ts_utc = DateTimeUtils.local_time_to_utc_epoch_us(
        _DECISION_TIME, trade_date
    )
    _require_feature_keys(
        feature_keys,
        trade_date=trade_date.isoformat(),
        decision_ts_utc=expected_decision_ts_utc,
    )
    entry_factor_by_symbol = entry_factors.set_index("symbol")["adj_factor"]
    exit_factor_by_symbol = exit_factors.set_index("symbol")["adj_factor"]

    symbols = feature_keys.column("symbol").to_pandas()
    entry_vwap_by_symbol = _label_window_vwap(
        entry_minutes,
        trade_date=trade_date,
    )
    exit_vwap_by_symbol = _label_window_vwap(
        exit_minutes,
        trade_date=next_trade_date,
    )
    entry_prices = apply_asof_price_adjustment(
        pd.DataFrame(
            {
                "raw_vwap": symbols.map(entry_vwap_by_symbol),
                "adj_factor": symbols.map(entry_factor_by_symbol),
            }
        ),
        adjustment="hfq",
        asof_date=trade_date.isoformat(),
        price_columns=("raw_vwap",),
        output_prefix="adjusted_",
    )["adjusted_raw_vwap"]
    exit_prices = apply_asof_price_adjustment(
        pd.DataFrame(
            {
                "raw_vwap": symbols.map(exit_vwap_by_symbol),
                "adj_factor": symbols.map(exit_factor_by_symbol),
            }
        ),
        adjustment="hfq",
        asof_date=next_trade_date.isoformat(),
        price_columns=("raw_vwap",),
        output_prefix="adjusted_",
    )["adjusted_raw_vwap"]
    gross_return = _positive_ratio(exit_prices, entry_prices) - 1.0
    return pa.Table.from_arrays(
        [
            *[feature_keys.column(name) for name in STOCK_1430_KEY_SCHEMA.names],
            pa.array(
                _percentile_rank(gross_return), type=pa.float64(), from_pandas=True
            ),
        ],
        schema=_LABEL_SCHEMA,
    )


def _require_feature_keys(
    feature_keys: pa.Table,
    *,
    trade_date: str,
    decision_ts_utc: int,
) -> None:
    table_ops.require_columns(
        feature_keys, STOCK_1430_KEY_SCHEMA.names, who="stock 14:30 Feature keys"
    )
    invalid_types = [
        field.name
        for field in STOCK_1430_KEY_SCHEMA
        if not feature_keys.schema.field(field.name).type.equals(field.type)
    ]
    if invalid_types:
        raise ValueError(
            f"stock 14:30 Feature keys: invalid column types: {invalid_types!r}"
        )
    table_ops.require_nonempty_strings(
        feature_keys,
        ("symbol", "trade_date"),
        who="stock 14:30 Feature keys",
    )
    table_ops.require_non_null(
        feature_keys,
        ("decision_ts_utc",),
        who="stock 14:30 Feature keys",
    )
    table_ops.require_unique(
        feature_keys,
        STOCK_1430_KEY_SCHEMA.names,
        who="stock 14:30 Feature keys",
    )
    if feature_keys.num_rows > 0:
        if (
            pc.all(
                pc.equal(
                    feature_keys.column("trade_date"),
                    pa.scalar(trade_date, type=pa.string()),
                )
            ).as_py()
            is not True
        ):
            raise ValueError("Feature trade_date does not match Label partition")
        if (
            pc.all(
                pc.equal(
                    feature_keys.column("decision_ts_utc"),
                    pa.scalar(decision_ts_utc, type=pa.int64()),
                )
            ).as_py()
            is not True
        ):
            raise ValueError("Feature decision_ts_utc does not match V1 grid")
    keys = feature_keys.select(STOCK_1430_KEY_SCHEMA.names)
    ordered_keys = keys.sort_by(
        [(column, "ascending") for column in STOCK_1430_KEY_SCHEMA.names]
    )
    if not keys.equals(ordered_keys):
        raise ValueError("Feature keys must use canonical order")


def _label_window_vwap(minutes: pa.Table, *, trade_date: date) -> pd.Series:
    start_ts_utc = DateTimeUtils.local_time_to_utc_epoch_us(
        _LABEL_WINDOW_START, trade_date
    )
    end_ts_utc = DateTimeUtils.local_time_to_utc_epoch_us(_LABEL_WINDOW_END, trade_date)
    selected = minutes.filter(
        pc.and_(
            pc.equal(
                minutes.column("phase"),
                pa.scalar(int(MarketPhase.CONTINUOUS), type=pa.int8()),
            ),
            pc.and_(
                pc.greater_equal(
                    minutes.column("minute_start_ts_utc"),
                    pa.scalar(start_ts_utc, type=pa.int64()),
                ),
                pc.less(
                    minutes.column("minute_start_ts_utc"),
                    pa.scalar(end_ts_utc, type=pa.int64()),
                ),
            ),
        )
    ).select(["symbol", "notional_sum", "volume_sum"])
    frame = selected.select(["symbol", "notional_sum"]).to_pandas()
    grouped = frame.groupby("symbol", sort=False, observed=True)
    return _positive_ratio(
        grouped["notional_sum"].sum(),
        _sum_window_integers(selected, columns=("volume_sum",))["volume_sum"],
    )


def _sum_window_integers(minutes: pa.Table, *, columns: Sequence[str]) -> pd.DataFrame:
    # H02 int64 values can exceed int64 over at most 60 observed minutes.
    # Keep decimal sums exact until division, or through trade-count ranking.
    widened = pa.table(
        {
            "symbol": minutes.column("symbol"),
            **{
                column: pc.cast(minutes.column(column), pa.decimal128(38, 0))
                for column in columns
            },
        }
    )
    return (
        widened.group_by("symbol", use_threads=False)
        .aggregate([(column, "sum") for column in columns])
        .rename_columns(["symbol", *columns])
        .to_pandas()
        .set_index("symbol")
    )


def _finite_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numeric_numerator = numerator.astype("float64")
    numeric_denominator = denominator.astype("float64")
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        ratio = numeric_numerator / numeric_denominator
    valid = (
        np.isfinite(numeric_numerator)
        & np.isfinite(numeric_denominator)
        & numeric_denominator.gt(0)
        & np.isfinite(ratio)
    )
    return ratio.where(valid)


def _positive_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    ratio = _finite_ratio(numerator, denominator)
    return ratio.where(ratio.gt(0))


def _percentile_rank(values: pd.Series) -> pd.Series:
    return values.rank(method="average", ascending=True, pct=True).astype("Float64")
