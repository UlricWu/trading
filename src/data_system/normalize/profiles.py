# filepath: src/data_system/normalize/profiles.py
"""Normalize profile definitions for raw-to-processed fact conversion."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src import logs
from src.utils import table_ops
from src.data_system.engines.trade_enrich_engine import TradeEnrichEngine
from src.data_system.normalize import security_type_resolver
from src.data_system.normalize.engine import (
    filter_trade_only,
    resolve_level2_event_spec,
)
from src.data_system.normalize.parser_engine import parse_events_arrow
from src.data_system.normalize.phase_resolver import PhaseResolver
from src.data_system.normalize.symbol_index_engine import SymbolIndexEngine
from src.utils.csv7z_batch_source import open_csv7z_batches
from src.utils.datetime_utils import DateTimeUtils


_TUSHARE_TARGET_SCHEMAS: Mapping[str, pa.Schema] = MappingProxyType(
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


@dataclass(frozen=True, slots=True)
class NormalizeOutput:
    """Materialized processed object returned by a normalize profile."""

    table: pa.Table
    symbol_slices: Mapping[str, range] | None = None

    def __post_init__(self) -> None:
        if self.symbol_slices is not None:
            object.__setattr__(
                self,
                "symbol_slices",
                MappingProxyType(dict(self.symbol_slices)),
            )


def normalize_tushare(
    *,
    input_file: Path,
    output_name: Path,
    raw_object: str | None = None,
    target_name: str = "",
    trade_date: str | None = None,
) -> NormalizeOutput:
    """Normalize one Tushare raw object into a processed dataset.

    Example:
        output = normalize_tushare(
            input_file=Path("/data/raw.parquet"),
            output_name=Path("/data/data.parquet"),
            raw_object="trade_calendar",
            target_name="trade_calendar",
            trade_date="2026-07-20",
        )
    """

    raw_df = pq.ParquetFile(input_file).read().to_pandas()
    logs.info(
        f"[FactNormalize] raw parquet read done output={output_name} "
        f"rows={len(raw_df)} "
    )
    out = raw_df.copy()

    if target_name == "trade_calendar":
        table_ops.require_columns(
            out,
            ("cal_date", "is_open"),
            who="trade_calendar",
        )
        if len(out) != 1:
            raise ValueError(
                f"trade_calendar must contain exactly one row, rows={len(out)}"
            )
        expected_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )
        calendar_date = DateTimeUtils.normalize_source_date(
            out.iloc[0]["cal_date"],
            field_name="cal_date",
        )
        if calendar_date != expected_date:
            raise ValueError(
                "trade_calendar date does not match partition: "
                f"expected={expected_date}, actual={calendar_date}"
            )
        open_value = pd.to_numeric(
            pd.Series([out.iloc[0]["is_open"]]),
            errors="coerce",
        ).iloc[0]
        if pd.isna(open_value) or open_value not in (0, 1):
            raise ValueError("trade_calendar is_open must be 0 or 1")
        out = pd.DataFrame(
            {
                "trade_date": [calendar_date],
                "is_open": [bool(open_value)],
            }
        )
    else:
        if "ts_code" in out.columns:
            out["symbol"] = out["ts_code"].astype("string").str.split(".").str[0]

        if "trade_date" in out.columns:
            trade_date_str = (
                out["trade_date"].astype("string").str.replace(".0", "", regex=False)
            )
            out["trade_date"] = trade_date_str.map(
                lambda value: DateTimeUtils.normalize_source_date(
                    value,
                    field_name="trade_date",
                )
            )

    if target_name == "stock_basic":
        table_ops.require_columns(out, ("list_date",), who="stock_basic")
        list_date_str = (
            out["list_date"].astype("string").str.replace(".0", "", regex=False)
        )
        out["list_date"] = list_date_str.map(
            lambda value: DateTimeUtils.normalize_source_date(
                value,
                field_name="list_date",
            )
        )

    table = _tushare_table_from_pandas(out, target_name=target_name)
    return NormalizeOutput(
        table=table,
    )


def _tushare_table_from_pandas(
    out: pd.DataFrame,
    *,
    target_name: str,
) -> pa.Table:
    schema = _TUSHARE_TARGET_SCHEMAS.get(target_name)
    if schema is None:
        return pa.Table.from_pandas(out, preserve_index=False)
    return pa.Table.from_pandas(out, schema=schema, preserve_index=False)


def normalize_level2(
    *,
    input_file: Path,
    output_name: Path,
    raw_object: str,
    trade_date: str,
    target_name: str = "",
) -> NormalizeOutput:
    """Normalize one configured Level-2 raw/output task into processed data.

    Example:
        output = normalize_level2(
            input_file=Path("/data/SH_Stock_OrderTrade.csv.7z"),
            output_name=Path("/data/sh_trade.parquet"),
            raw_object="SH_Stock_OrderTrade",
            trade_date="2026-07-27",
            target_name="sh_trade",
        )
    """

    spec = resolve_level2_event_spec(
        raw_object=raw_object,
        output=target_name,
    )
    logs.info(f"[NormalizeLevel2] resolved spec={spec}")

    tables: list[pa.Table] = []
    batch_count = 1
    raw_rows = 0
    parsed_rows = 0

    with open_csv7z_batches(input_file) as record_batches:
        for record_batch in record_batches:
            table = pa.Table.from_batches([record_batch])
            table = parse_events_arrow(
                table=table,
                exchange=spec.exchange,
                kind=spec.kind,
            )

            raw_rows += table.num_rows

            table = filter_trade_only(table)
            if table.num_rows == 0:
                continue

            table = security_type_resolver.execute(table=table, exchange=spec.exchange)

            parsed_rows += table.num_rows

            if batch_count % 12 == 0:
                logs.info(
                    f"[FactNormalize] batch progress  "
                    f"batches={batch_count} "
                    f"raw_rows={raw_rows} -> parsed_rows={parsed_rows}"
                )

            tables.append(table)
            batch_count += 1

    if not tables:
        return NormalizeOutput(table=pa.table({}))
    big_tables = pa.concat_tables(tables)
    logs.info(f"big_tables.num_rows={big_tables.num_rows}")

    phase_annotator = PhaseResolver()
    big_tables = phase_annotator.resolve(
        table=big_tables,
        exchange=spec.exchange,
        trade_date=trade_date,
    )

    big_tables, symbol_slices = SymbolIndexEngine.execute(big_tables)
    if not symbol_slices or big_tables.num_rows == 0:
        raise RuntimeError("cannot enrich Level-2 trade table without symbol index")

    if spec.kind == "trade":
        indexed_row_count = big_tables.num_rows
        enrich_engine = TradeEnrichEngine()
        big_tables = pa.concat_tables(
            [
                enrich_engine.execute(big_tables.slice(rows.start, len(rows)))
                for rows in symbol_slices.values()
            ]
        )
        if big_tables.num_rows != indexed_row_count:
            raise RuntimeError(
                "Level-2 enrichment changed row count after symbol indexing"
            )

    logs.info(
        f"[FactNormalize] finalize done "
        f"rows={big_tables.num_rows} "
        f"exchange={spec.exchange} "
        f"kind={spec.kind} "
        f"symbols={len(symbol_slices)} "
        f"output={output_name} "
    )
    return NormalizeOutput(
        table=big_tables,
        symbol_slices=symbol_slices,
    )


NORMALIZE_PROFILES: Mapping[tuple[str, str], Callable[..., NormalizeOutput]] = (
    MappingProxyType(
        {
            ("tushare", "v1"): normalize_tushare,
            ("level2_ftp", "v1"): normalize_level2,
        }
    )
)
