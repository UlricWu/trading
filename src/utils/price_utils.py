# filepath: src/utils/price_utils.py
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
import pandas as pd

from src.utils import table_ops
from src.utils.datetime_utils import DateTimeUtils

PriceAdjustment = Literal["raw", "qfq", "hfq"]


def _validate_price_columns(
    frame: pd.DataFrame,
    price_columns: Sequence[str],
) -> tuple[str, ...]:
    if isinstance(price_columns, (str, bytes)) or not isinstance(
        price_columns, Sequence
    ):
        raise TypeError("field 'price_columns' must be a sequence of strings")

    owned_price_columns = tuple(price_columns)
    if any(not isinstance(column, str) or not column for column in owned_price_columns):
        raise TypeError("field 'price_columns' must contain non-empty strings")
    if len(set(owned_price_columns)) != len(owned_price_columns):
        raise ValueError("field 'price_columns' must not contain duplicates")

    if owned_price_columns:
        table_ops.require_columns(
            frame,
            owned_price_columns,
            who="price adjustment",
        )
    return owned_price_columns


def _calculate_qfq_price_scale(
    owned_frame: pd.DataFrame,
    adjustment_factor: pd.Series,
    *,
    validated_asof_date: str,
) -> pd.Series:
    table_ops.require_nonempty_strings(
        owned_frame,
        ("symbol", "trade_date"),
        who="qfq",
    )

    trade_dates = owned_frame["trade_date"]
    if not owned_frame.empty:
        valid_trade_date_format = trade_dates.str.fullmatch(
            r"\d{4}-\d{2}-\d{2}",
            na=False,
        )
        parsed_trade_dates = pd.to_datetime(
            trade_dates,
            format="%Y-%m-%d",
            errors="coerce",
        )
        canonical_trade_dates = parsed_trade_dates.dt.strftime("%Y-%m-%d").eq(
            trade_dates
        )
        if not (
            valid_trade_date_format & parsed_trade_dates.notna() & canonical_trade_dates
        ).all():
            raise ValueError(
                "column 'trade_date' must contain valid YYYY-MM-DD values for qfq"
            )

    asof_factors = owned_frame.loc[
        owned_frame["trade_date"] == validated_asof_date,
        ["symbol", "adj_factor"],
    ]
    table_ops.require_unique(
        asof_factors,
        ("symbol",),
        who="qfq as-of factors",
    )

    input_symbols = pd.Index(owned_frame["symbol"].unique())
    asof_symbols = pd.Index(asof_factors["symbol"])
    missing_asof_symbols = input_symbols.difference(asof_symbols)
    if not missing_asof_symbols.empty:
        raise ValueError(
            "qfq requires one as-of factor per symbol; "
            f"missing symbols={missing_asof_symbols.tolist()} "
            f"asof_date={validated_asof_date}"
        )

    asof_factor_by_symbol = asof_factors.set_index("symbol")["adj_factor"]
    asof_factor = pd.to_numeric(
        owned_frame["symbol"].map(asof_factor_by_symbol),
        errors="coerce",
    )
    valid_adjustment_factor = np.isfinite(adjustment_factor) & adjustment_factor.gt(0)
    valid_asof_factor = np.isfinite(asof_factor) & asof_factor.gt(0)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore", under="ignore"):
        return adjustment_factor.where(valid_adjustment_factor) / asof_factor.where(
            valid_asof_factor
        )


def apply_asof_price_adjustment(
    frame: pd.DataFrame,
    *,
    adjustment: PriceAdjustment,
    asof_date: str,
    price_columns: Sequence[str] = ("open", "high", "low", "close"),
    output_prefix: str = "",
) -> pd.DataFrame:
    """Return the price view defined by the adjustment owner contract.

    ``raw`` copies prices unchanged, ``hfq`` multiplies each price by its row
    factor, and ``qfq`` divides that value by the same symbol's unique factor
    on ``asof_date``. Adjusted outputs are positive finite numbers or null. The
    input frame is never mutated. The full field and ownership contract is in
    ``docs/data/price_adjustment_contract.md``.

    Example:
        adjusted = apply_asof_price_adjustment(
            pd.DataFrame({"close": [10.0]}),
            adjustment="raw",
            asof_date="2026-07-15",
            price_columns=("close",),
        )
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("field 'frame' must be a pandas.DataFrame")
    if adjustment not in {"raw", "qfq", "hfq"}:
        raise ValueError(f"field 'adjustment' has unsupported value: {adjustment}")
    if not isinstance(output_prefix, str):
        raise TypeError("field 'output_prefix' must be a string")
    validated_asof_date = DateTimeUtils.require_system_date(
        asof_date,
        field_name="asof_date",
    )
    owned_price_columns = _validate_price_columns(frame, price_columns)

    owned_frame = frame.copy()
    if adjustment == "raw":
        if output_prefix:
            for column in owned_price_columns:
                owned_frame.loc[:, f"{output_prefix}{column}"] = owned_frame[column]
        return owned_frame

    table_ops.require_columns(
        owned_frame,
        ("adj_factor",),
        who="price adjustment",
    )
    adjustment_factor = pd.to_numeric(
        owned_frame["adj_factor"],
        errors="coerce",
    )

    if adjustment == "hfq":
        price_scale = adjustment_factor
    else:
        price_scale = _calculate_qfq_price_scale(
            owned_frame,
            adjustment_factor,
            validated_asof_date=validated_asof_date,
        )

    valid_scale = np.isfinite(price_scale) & price_scale.gt(0)
    for column in owned_price_columns:
        target_column = f"{output_prefix}{column}" if output_prefix else column
        numeric_price = pd.to_numeric(owned_frame[column], errors="coerce")
        valid_price = np.isfinite(numeric_price) & numeric_price.gt(0)
        with np.errstate(invalid="ignore", over="ignore", under="ignore"):
            adjusted_price = numeric_price * price_scale
        valid_adjusted_price = np.isfinite(adjusted_price) & adjusted_price.gt(0)
        owned_frame.loc[:, target_column] = adjusted_price.where(
            valid_price & valid_scale & valid_adjusted_price
        )

    return owned_frame


__all__ = ["apply_asof_price_adjustment"]
