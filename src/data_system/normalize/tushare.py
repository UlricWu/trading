# filepath: src/data_system/normalize/tushare.py
"""Normalize structured Tushare payloads into processed tables."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src import logs
from src.data_system.normalize import NormalizeOutput
from src.utils import table_ops
from src.utils.datetime_utils import DateTimeUtils

_TARGET_SCHEMAS: Mapping[str, pa.Schema] = MappingProxyType(
    {
        "trade_calendar": pa.schema(
            [
                ("trade_date", pa.string()),
                ("is_open", pa.bool_()),
            ]
        ),
        "daily_bar": pa.schema(
            [
                ("ts_code", pa.string()),
                ("trade_date", pa.string()),
                ("open", pa.float64()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
                ("pre_close", pa.float64()),
                ("change", pa.float64()),
                ("pct_chg", pa.float64()),
                ("vol", pa.int64()),
                ("amount", pa.float64()),
                ("ah_vol", pa.int64()),
                ("ah_amount", pa.float64()),
                ("symbol", pa.string()),
            ]
        ),
    }
)

# Source units belong to docs/data/source_contract.md and its dataset owners.
# Identically named fields need different factors.
_UNIT_MULTIPLIERS: Mapping[str, Mapping[str, float]] = MappingProxyType({
    "daily_bar": MappingProxyType({"amount": 1_000.0, "ah_amount": 1_000.0, "pct_chg": 0.01}),
    "daily_basic": MappingProxyType({
        "total_share": 10_000.0, "float_share": 10_000.0, "free_share": 10_000.0,
        "total_mv": 10_000.0, "circ_mv": 10_000.0,
        "turnover_rate": 0.01, "turnover_rate_f": 0.01, "dv_ratio": 0.01, "dv_ttm": 0.01,
    }),
    "stock_basic": MappingProxyType({
        "total_share": 100_000_000.0, "float_share": 100_000_000.0,
        "rev_yoy": 0.01, "profit_yoy": 0.01, "gpr": 0.01, "npr": 0.01,
    }),
    "moneyflow": MappingProxyType({
        **{f"{side}_{size}_amount": 10_000.0
           for side in ("buy", "sell") for size in ("sm", "md", "lg", "elg")},
        "net_mf_amount": 10_000.0,
    }),
    "dividend": MappingProxyType({"base_share": 10_000.0}),
})


def normalize_tushare(
    *,
    input_file: Path,
    output_name: Path,
    raw_object: str,
    target_name: str,
    trade_date: str | None = None,
) -> NormalizeOutput:
    """Normalize one structured Tushare raw object.

    ``trade_date=None`` identifies the annual trade-calendar object; daily
    Tushare objects receive their partition date from the fact step. Confirmed
    units become shares, yuan and fractional rates; raw payloads stay unchanged.

    Example:
        output = normalize_tushare(
            input_file=Path("/data/raw.parquet"),
            output_name=Path("/data/data.parquet"),
            raw_object="daily_bar",
            target_name="daily_bar",
            trade_date="2026-07-27",
        )
    """
    raw_frame = pq.ParquetFile(input_file).read().to_pandas(types_mapper=pd.ArrowDtype)
    logs.info(
        f"▶️ Tushare normalize; "
        f"target={target_name} trade_date={trade_date} "
        f"output={output_name} rows={len(raw_frame)}"
    )
    normalized_frame = raw_frame.copy()

    if target_name == "trade_calendar":
        table_ops.require_columns(
            normalized_frame,
            ("cal_date", "is_open"),
            who="trade_calendar",
        )
        normalized_frame = pd.DataFrame(
            {
                "trade_date": normalized_frame["cal_date"].map(
                    lambda value: DateTimeUtils.normalize_source_date(
                        value,
                        field_name="cal_date",
                    )
                ),
                "is_open": normalized_frame["is_open"].eq(1),
            }
        )
    else:
        if "ts_code" in normalized_frame.columns:
            normalized_frame["symbol"] = (
                normalized_frame["ts_code"].astype("string").str.split(".").str[0]
            )

        if "trade_date" in normalized_frame.columns:
            source_dates = (
                normalized_frame["trade_date"]
                .astype("string")
                .str.replace(".0", "", regex=False)
            )
            normalized_frame["trade_date"] = source_dates.map(
                lambda value: DateTimeUtils.normalize_source_date(
                    value,
                    field_name="trade_date",
                )
            )

    if (
        len(normalized_frame) == 0
        and target_name in ("stock_basic", "stock_st", "suspend_d", "dividend")
        and "symbol" not in normalized_frame.columns
    ):
        normalized_frame["symbol"] = pd.Series(dtype="string")

    if target_name == "daily_bar":
        for column in ("ah_vol", "ah_amount"):
            if column not in normalized_frame.columns:
                normalized_frame[column] = pd.Series(float("nan"), index=normalized_frame.index)

    if target_name == "dividend":
        for column in ("base_date", "base_share"):
            if column not in normalized_frame.columns:
                normalized_frame[column] = pd.Series(None, index=normalized_frame.index, dtype="object")

    for column, multiplier in _UNIT_MULTIPLIERS.get(target_name, {}).items():
        if column in normalized_frame.columns:
            try:
                normalized_frame[column] = pd.to_numeric(normalized_frame[column]).astype("float64") * multiplier
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{target_name}.{column}: cannot normalize numeric units") from exc
            if pc.all(pc.is_finite(pa.array(normalized_frame[column]))).as_py() is False:
                raise ValueError(f"{target_name}.{column}: normalized values must be finite")

    volume_columns = ()
    if target_name == "daily_bar":
        volume_columns = ("vol", "ah_vol")
    elif target_name == "moneyflow":
        volume_columns = tuple(f"{side}_{size}_vol" for side in ("buy", "sell")
                               for size in ("sm", "md", "lg", "elg")) + ("net_mf_vol",)
    for column in volume_columns:
        if column not in normalized_frame.columns:
            continue
        try:
            # Two decimal places in lots express whole shares. Decimal kernels
            # reject fractional shares and avoid binary products such as 0.29 * 100.
            lots = pc.cast(pa.array(normalized_frame[column].astype("string")), pa.decimal128(21, 2))
            shares = pc.cast(pc.multiply_checked(lots,
                pa.scalar(Decimal(100), type=pa.decimal128(3, 0))), pa.int64())
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise ValueError(f"{target_name}.{column}: quantity must convert exactly to int64 shares") from exc
        if target_name == "daily_bar" and pc.any(pc.less(shares, 0)).as_py():
            raise ValueError(f"{target_name}.{column}: shares must be non-negative")
        normalized_frame[column] = pd.Series(shares, dtype=pd.ArrowDtype(pa.int64()), index=normalized_frame.index)

    if target_name == "dividend":
        columns = ("ts_code", "symbol", "end_date", "ann_date", "div_proc", "imp_ann_date", "record_date", "ex_date",
                   "pay_date", "div_listdate", "cash_div_tax", "stk_div", "base_date", "base_share")
        if normalized_frame.empty:
            normalized_frame = pd.DataFrame({column: pd.Series(dtype="float64" if column == "base_share" else "string")
                                             for column in columns})
        else:
            table_ops.require_columns(normalized_frame, columns, who="dividend")
            normalized_frame = normalized_frame.loc[:, columns].copy()
            normalized_frame["div_proc"] = normalized_frame["div_proc"].str.strip()
            for column in ("end_date", "ann_date", "imp_ann_date", "record_date", "ex_date", "pay_date", "div_listdate", "base_date"):
                normalized_frame[column] = normalized_frame[column].map(
                    lambda value: None if pd.isna(value) else DateTimeUtils.normalize_source_date(value, field_name=column))
            for column in ("cash_div_tax", "stk_div"):
                normalized_frame[column] = normalized_frame[column].map(lambda value: None if pd.isna(value) else str(value))

    if target_name == "stock_basic":
        if len(normalized_frame) == 0:
            if "list_date" not in normalized_frame.columns:
                normalized_frame["list_date"] = pd.Series(dtype="string")
        else:
            table_ops.require_columns(
                normalized_frame,
                ("list_date",),
                who="stock_basic",
            )
        normalized_frame["list_date"] = pd.to_datetime(
            normalized_frame["list_date"],
            format="%Y%m%d",
            errors="coerce",
        ).dt.strftime("%Y-%m-%d")

    schema = _TARGET_SCHEMAS.get(target_name)
    if schema is None:
        table = pa.Table.from_pandas(normalized_frame, preserve_index=False)
    else:
        table = pa.Table.from_pandas(
            normalized_frame,
            schema=schema,
            preserve_index=False,
        )
    return NormalizeOutput(table=table)
