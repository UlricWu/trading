# filepath: src/data_system/normalize/tushare.py
"""Normalize structured Tushare payloads into processed tables."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pandas as pd
import pyarrow as pa
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
                ("vol", pa.float64()),
                ("amount", pa.float64()),
                ("symbol", pa.string()),
            ]
        ),
    }
)


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
    Tushare objects receive their partition date from the fact step.

    Example:
        output = normalize_tushare(
            input_file=Path("/data/raw.parquet"),
            output_name=Path("/data/data.parquet"),
            raw_object="daily_bar",
            target_name="daily_bar",
            trade_date="2026-07-27",
        )
    """
    raw_frame = pq.ParquetFile(input_file).read().to_pandas()
    logs.info(
        f"raw parquet read done; raw_object={raw_object} "
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

    if target_name == "stock_basic":
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
