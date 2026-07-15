# filepath: src/utils/price_utils.py
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import pandas as pd

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

    missing_price_columns = [
        column for column in owned_price_columns if column not in frame.columns
    ]
    if missing_price_columns:
        raise ValueError(f"missing price columns: {missing_price_columns}")
    return owned_price_columns


def _calculate_qfq_price_scale(
    owned_frame: pd.DataFrame,
    adjustment_factor: pd.Series,
    *,
    asof_date: str,
) -> pd.Series:
    validated_asof_date = DateTimeUtils.require_trade_date(asof_date)
    missing_qfq_columns = [
        column
        for column in ("symbol", "trade_date")
        if column not in owned_frame.columns
    ]
    if missing_qfq_columns:
        raise ValueError(f"missing qfq columns: {missing_qfq_columns}")
    if owned_frame["symbol"].isna().any():
        raise ValueError("column 'symbol' must not contain null values for qfq")
    if not owned_frame.empty and (
        pd.api.types.infer_dtype(
            owned_frame["symbol"],
            skipna=False,
        )
        != "string"
        or not owned_frame["symbol"].str.len().gt(0).all()
    ):
        raise TypeError("column 'symbol' must contain non-empty strings for qfq")

    asof_factors = owned_frame.loc[
        owned_frame["trade_date"] == validated_asof_date,
        ["symbol", "adj_factor"],
    ]
    if asof_factors["symbol"].duplicated().any():
        raise ValueError(
            "qfq requires one as-of factor per symbol; "
            f"duplicates found for asof_date={validated_asof_date}"
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
    row_asof_factor = owned_frame["symbol"].map(asof_factor_by_symbol)
    return adjustment_factor / pd.to_numeric(
        row_asof_factor,
        errors="coerce",
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
    on ``asof_date``. Non-positive or non-numeric prices and factors produce
    null adjusted values. The input frame is never mutated. The full field and
    ownership contract is in ``docs/data/price_adjustment_contract.md``.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("field 'frame' must be a pandas.DataFrame")
    if adjustment not in {"raw", "qfq", "hfq"}:
        raise ValueError(f"field 'adjustment' has unsupported value: {adjustment}")
    if not isinstance(output_prefix, str):
        raise TypeError("field 'output_prefix' must be a string")
    owned_price_columns = _validate_price_columns(frame, price_columns)

    owned_frame = frame.copy()
    if adjustment == "raw":
        if output_prefix:
            for column in owned_price_columns:
                owned_frame.loc[:, f"{output_prefix}{column}"] = owned_frame[column]
        return owned_frame

    if "adj_factor" not in owned_frame.columns:
        raise ValueError("missing required column: adj_factor")
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
            asof_date=asof_date,
        )

    valid_scale = pd.to_numeric(price_scale, errors="coerce").gt(0)
    for column in owned_price_columns:
        target_column = f"{output_prefix}{column}" if output_prefix else column
        numeric_price = pd.to_numeric(owned_frame[column], errors="coerce")
        owned_frame.loc[:, target_column] = (numeric_price * price_scale).where(
            numeric_price.gt(0) & valid_scale
        )

    return owned_frame


__all__ = ["apply_asof_price_adjustment"]
