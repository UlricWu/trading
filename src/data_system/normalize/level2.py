# filepath: src/data_system/normalize/level2.py
"""Normalize admitted Level-2 trade payloads into indexed daily tables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import pyarrow as pa
import pyarrow.compute as pc

from src import logs
from src.data_system.normalize import NormalizeOutput
from src.data_system.normalize.level2_phase import resolve_level2_phase
from src.data_system.normalize.level2_security import resolve_level2_security_type
from src.utils import table_ops
from src.utils.csv7z_batch_source import open_csv7z_batches
from src.utils.datetime_utils import DateTimeUtils

_PARSED_TRADE_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("ts_utc", pa.int64()),
        ("event", pa.string()),
        ("order_id", pa.int64()),
        ("main_seq", pa.int64()),
        ("sub_seq", pa.int64()),
        ("side", pa.string()),
        ("price", pa.float64()),
        ("volume", pa.int64()),
        ("buy_no", pa.int64()),
        ("sell_no", pa.int64()),
    ]
)


@dataclass(frozen=True, slots=True)
class Level2TradeSpec:
    """Describe one admitted Level-2 trade route and its source fields.

    Example:
        spec = Level2TradeSpec(
            raw_object="SZ_Trade",
            output="sz_trade",
            exchange="sz",
            symbol_field="SecurityID",
            time_field="TickTime",
            event_field="ExecType",
            event_mapping={"1": "TRADE", "2": "CANCEL"},
            price_field="TradePrice",
            volume_field="TradeVolume",
            side_field=None,
            side_mapping=None,
            buy_no_field="BuyNo",
            sell_no_field="SellNo",
        )
    """

    raw_object: str
    output: str
    exchange: Literal["sh", "sz"]
    symbol_field: str
    time_field: str
    event_field: str
    event_mapping: Mapping[str, str]
    price_field: str
    volume_field: str
    side_field: str | None
    side_mapping: Mapping[str, str] | None
    buy_no_field: str | None
    sell_no_field: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_mapping",
            MappingProxyType(dict(self.event_mapping)),
        )
        if self.side_mapping is not None:
            object.__setattr__(
                self,
                "side_mapping",
                MappingProxyType(dict(self.side_mapping)),
            )


_TRADE_SPECS: Mapping[tuple[str, str], Level2TradeSpec] = MappingProxyType(
    {
        ("SH_Stock_OrderTrade", "sh_trade"): Level2TradeSpec(
            raw_object="SH_Stock_OrderTrade",
            output="sh_trade",
            exchange="sh",
            symbol_field="SecurityID",
            time_field="TickTime",
            event_field="TickType",
            event_mapping={"T": "TRADE"},
            price_field="Price",
            volume_field="Volume",
            side_field="Side",
            side_mapping={"1": "B", "2": "S"},
            buy_no_field="BuyNo",
            sell_no_field="SellNo",
        ),
        ("SZ_Trade", "sz_trade"): Level2TradeSpec(
            raw_object="SZ_Trade",
            output="sz_trade",
            exchange="sz",
            symbol_field="SecurityID",
            time_field="TickTime",
            event_field="ExecType",
            event_mapping={"1": "TRADE", "2": "CANCEL"},
            price_field="TradePrice",
            volume_field="TradeVolume",
            side_field=None,
            side_mapping=None,
            buy_no_field="BuyNo",
            sell_no_field="SellNo",
        ),
    }
)


def normalize_level2(
    *,
    input_file: Path,
    output_name: Path,
    raw_object: str,
    trade_date: str,
    target_name: str,
) -> NormalizeOutput:
    """Normalize one configured Level-2 trade object.

    Example:
        output = normalize_level2(
            input_file=Path("/data/SH_Stock_OrderTrade.csv.7z"),
            output_name=Path("/data/sh_trade.parquet"),
            raw_object="SH_Stock_OrderTrade",
            trade_date="2026-07-27",
            target_name="sh_trade",
        )
    """
    spec = _TRADE_SPECS.get((raw_object, target_name))
    if spec is None:
        raise ValueError(
            "unsupported Level-2 raw/output normalize target: "
            f"raw_object={raw_object!r}, output={target_name!r}"
        )
    logs.info(f"Level-2 route resolved; spec={spec}")
    trade_date = DateTimeUtils.require_system_date(
        trade_date,
        field_name="trade_date",
    )

    parsed_batches: list[pa.Table] = []
    nonempty_batch_count = 0
    raw_row_count = 0
    parsed_row_count = 0

    with open_csv7z_batches(input_file) as record_batches:
        for record_batch in record_batches:
            raw_table = pa.Table.from_batches([record_batch])
            raw_row_count += raw_table.num_rows
            parsed_table = parse_level2_trade_batch(
                raw_table,
                spec=spec,
                trade_date=trade_date,
            )
            if parsed_table.num_rows == 0:
                continue

            parsed_table = resolve_level2_security_type(
                parsed_table,
                exchange=spec.exchange,
            )
            parsed_batches.append(parsed_table)
            parsed_row_count += parsed_table.num_rows
            nonempty_batch_count += 1

            if nonempty_batch_count % 64 == 0:
                logs.info(
                    f"batches={nonempty_batch_count} "
                    f"raw_rows={raw_row_count} parsed_rows={parsed_row_count}"
                )

    if not parsed_batches:
        return NormalizeOutput(table=pa.table({}))

    processed_day = build_processed_level2_trade_day(
        pa.concat_tables(parsed_batches),
        exchange=spec.exchange,
        trade_date=trade_date,
    )
    symbol_count = len(processed_day.symbol_slices or {})
    logs.info(
        f"Level-2 normalize done; rows={processed_day.table.num_rows} "
        f"exchange={spec.exchange} symbols={symbol_count} output={output_name}"
    )
    return processed_day


def parse_level2_trade_batch(
    table: pa.Table,
    *,
    spec: Level2TradeSpec,
    trade_date: str,
) -> pa.Table:
    """Parse and filter one source batch into normalized Level-2 trades.

    Example:
        spec = Level2TradeSpec(
            raw_object="SH_Stock_OrderTrade",
            output="sh_trade",
            exchange="sh",
            symbol_field="SecurityID",
            time_field="TickTime",
            event_field="TickType",
            event_mapping={"T": "TRADE"},
            price_field="Price",
            volume_field="Volume",
            side_field="Side",
            side_mapping={"1": "B", "2": "S"},
            buy_no_field="BuyNo",
            sell_no_field="SellNo",
        )
        trades = parse_level2_trade_batch(
            raw_table,
            spec=spec,
            trade_date="2026-07-27",
        )
    """
    if table.num_rows == 0:
        return pa.table(
            {
                field.name: pa.array([], type=field.type)
                for field in _PARSED_TRADE_SCHEMA
            }
        )

    required_fields = (
        "TradeTime",
        spec.symbol_field,
        spec.time_field,
        spec.event_field,
        spec.price_field,
        spec.volume_field,
        "MainSeq",
        "SubSeq",
        *((spec.side_field,) if spec.side_field and spec.side_mapping else ()),
        *((spec.buy_no_field,) if spec.buy_no_field else ()),
        *((spec.sell_no_field,) if spec.sell_no_field else ()),
    )
    table_ops.require_columns(
        table,
        required_fields,
        who=f"Level-2 {spec.exchange} trade",
    )
    _require_trade_time_date(table["TradeTime"], trade_date=trade_date)

    event = _map_values_or_null(
        table[spec.event_field],
        spec.event_mapping,
    )
    if spec.side_field and spec.side_mapping:
        side = _map_values_or_null(
            table[spec.side_field],
            spec.side_mapping,
        )
    else:
        side = pa.nulls(table.num_rows)

    buy_no = (
        table[spec.buy_no_field]
        if spec.buy_no_field
        else pa.repeat(pa.scalar(0, type=pa.int64()), table.num_rows)
    )
    sell_no = (
        table[spec.sell_no_field]
        if spec.sell_no_field
        else pa.repeat(pa.scalar(0, type=pa.int64()), table.num_rows)
    )
    main_seq = pc.cast(table["MainSeq"], pa.int64())
    sub_seq = pc.cast(table["SubSeq"], pa.int64())
    normalized = pa.table(
        {
            "symbol": table[spec.symbol_field],
            "ts_utc": _exchange_time_to_utc_epoch_us(
                table[spec.time_field],
                trade_date=trade_date,
                exchange=spec.exchange,
            ),
            "event": event,
            "order_id": sub_seq,
            "main_seq": main_seq,
            "sub_seq": sub_seq,
            "side": side,
            "price": pc.cast(table[spec.price_field], pa.float64()),
            "volume": pc.cast(table[spec.volume_field], pa.int64()),
            "buy_no": pc.cast(buy_no, pa.int64()),
            "sell_no": pc.cast(sell_no, pa.int64()),
        }
    ).cast(_PARSED_TRADE_SCHEMA)

    valid_trade = pc.and_(
        pc.equal(normalized["event"], pa.scalar("TRADE", type=pa.string())),
        pc.and_(
            pc.greater(normalized["price"], pa.scalar(0)),
            pc.greater(normalized["volume"], pa.scalar(0)),
        ),
    )
    return normalized.filter(valid_trade)


def build_processed_level2_trade_day(
    table: pa.Table,
    *,
    exchange: Literal["sh", "sz"],
    trade_date: str,
) -> NormalizeOutput:
    """Build one processed Level-2 trade day from all parsed source batches.

    Example:
        processed_day = build_processed_level2_trade_day(
            parsed_trades,
            exchange="sh",
            trade_date="2026-07-27",
        )
    """
    phased_table = resolve_level2_phase(
        table=table,
        exchange=exchange,
        trade_date=trade_date,
    )
    indexed_table, symbol_slices = _build_symbol_index(phased_table)
    if not symbol_slices or indexed_table.num_rows == 0:
        raise RuntimeError("cannot enrich Level-2 trade table without symbol index")

    indexed_row_count = indexed_table.num_rows
    enriched_table = pa.concat_tables(
        [
            _enrich_symbol_trades(indexed_table.slice(rows.start, len(rows)))
            for rows in symbol_slices.values()
        ]
    )
    if enriched_table.num_rows != indexed_row_count:
        raise RuntimeError("Level-2 enrichment changed row count after symbol indexing")
    return NormalizeOutput(
        table=enriched_table,
        symbol_slices=symbol_slices,
    )


def _build_symbol_index(table: pa.Table) -> tuple[pa.Table, dict[str, range]]:
    table_ops.require_columns(
        table,
        ("symbol", "ts_utc", "main_seq", "sub_seq"),
        who="Level-2 symbol index",
    )
    table_ops.require_nonempty_strings(
        table,
        ("symbol",),
        who="Level-2 symbol index",
    )

    symbol = table["symbol"]
    if pa.types.is_dictionary(symbol.type):
        table = table.set_column(
            table.column_names.index("symbol"),
            "symbol",
            pc.cast(symbol, pa.string()),
        )
    elif not pa.types.is_string(symbol.type):
        raise TypeError(f"Level-2 symbol index has invalid symbol type: {symbol.type}")

    timestamp = table["ts_utc"]
    if not (
        pa.types.is_integer(timestamp.type) or pa.types.is_timestamp(timestamp.type)
    ):
        raise TypeError(
            f"Level-2 symbol index has invalid ts_utc type: {timestamp.type}"
        )

    for sequence_column in ("main_seq", "sub_seq"):
        sequence = table[sequence_column]
        if not pa.types.is_integer(sequence.type):
            raise TypeError(
                f"Level-2 symbol index has invalid {sequence_column} type: "
                f"{sequence.type}"
            )
        if sequence.null_count:
            raise ValueError(
                f"Level-2 symbol index {sequence_column} must not contain null"
            )

    row_count = table.num_rows
    logs.info(f"Level-2 sort started; rows={row_count}")
    sorted_table = table.take(
        pc.sort_indices(
            table,
            sort_keys=[
                ("symbol", "ascending"),
                ("ts_utc", "ascending"),
                ("main_seq", "ascending"),
                ("sub_seq", "ascending"),
            ],
        )
    )
    logs.info(f"Level-2 sort done; rows={row_count}")

    encoded = pc.run_end_encode(sorted_table["symbol"]).combine_chunks()
    run_ends = encoded.run_ends.to_pylist()
    symbols = encoded.values.to_pylist()
    symbol_slices: dict[str, range] = {}
    start = 0
    for symbol_value, end_exclusive in zip(symbols, run_ends):
        end = int(end_exclusive)
        symbol_slices[symbol_value] = range(start, end)
        start = end

    logs.info(f"Level-2 symbol index done; symbols={len(symbol_slices)}")
    return sorted_table, symbol_slices


def _enrich_symbol_trades(table: pa.Table) -> pa.Table:
    table_ops.require_columns(
        table,
        ("price", "volume"),
        who="Level-2 trade enrichment",
    )
    price = table["price"].combine_chunks()
    volume = table["volume"]
    notional = pc.multiply(
        pc.cast(price, pa.float64()),
        pc.cast(volume, pa.float64()),
    )

    previous_price = pa.concat_arrays(
        [
            pa.array([None], type=price.type),
            price.slice(0, len(price) - 1),
        ]
    )
    difference = pc.subtract(
        pc.cast(price, pa.float64()),
        pc.cast(previous_price, pa.float64()),
    )
    trade_side = pc.cast(
        pc.fill_null(
            pc.if_else(
                pc.greater(difference, 0),
                pa.scalar(1, pa.int8()),
                pc.if_else(
                    pc.less(difference, 0),
                    pa.scalar(-1, pa.int8()),
                    pa.scalar(0, pa.int8()),
                ),
            ),
            pa.scalar(0, pa.int8()),
        ),
        pa.int8(),
    )
    return table.append_column("notional", notional).append_column(
        "trade_side",
        trade_side,
    )


def _require_trade_time_date(
    values: pa.Array | pa.ChunkedArray,
    *,
    trade_date: str,
) -> None:
    if not pa.types.is_string(values.type):
        raise TypeError("TradeTime must be a string column")
    if values.null_count:
        raise ValueError("TradeTime must not contain null values")

    raw_dates = pc.utf8_slice_codeunits(values, start=0, stop=10)
    if pc.any(pc.not_equal(raw_dates, pa.scalar(trade_date))).as_py():
        raise ValueError(f"TradeTime date must match trade_date={trade_date}")


def _exchange_time_to_utc_epoch_us(
    values: pa.Array | pa.ChunkedArray,
    *,
    trade_date: str,
    exchange: Literal["sh", "sz"],
) -> pa.Array | pa.ChunkedArray:
    source_name = f"{exchange.upper()} TickTime"
    if not pa.types.is_string(values.type):
        raise TypeError(f"{source_name} must be a string column")
    if values.null_count:
        raise ValueError(f"{source_name} must not contain null values")

    source_width = 8 if exchange == "sh" else 9
    precision = "HHMMSScc" if exchange == "sh" else "HHMMSSsss"
    valid_digits = pc.match_substring_regex(
        values,
        rf"^\d{{1,{source_width}}}$",
    )
    if pc.any(pc.invert(valid_digits)).as_py():
        raise ValueError(
            f"{source_name} must contain 1-{source_width} digits as {precision}"
        )

    canonical_time = pc.utf8_lpad(values, source_width, "0")
    if exchange == "sh":
        canonical_time = pc.binary_join_element_wise(
            canonical_time,
            pa.scalar("0"),
            pa.scalar(""),
        )

    hour = pc.cast(
        pc.utf8_slice_codeunits(canonical_time, start=0, stop=2),
        pa.int64(),
    )
    minute = pc.cast(
        pc.utf8_slice_codeunits(canonical_time, start=2, stop=4),
        pa.int64(),
    )
    second = pc.cast(
        pc.utf8_slice_codeunits(canonical_time, start=4, stop=6),
        pa.int64(),
    )
    millisecond = pc.cast(
        pc.utf8_slice_codeunits(canonical_time, start=6, stop=9),
        pa.int64(),
    )
    invalid_clock = pc.or_(
        pc.greater(hour, pa.scalar(23, pa.int64())),
        pc.or_(
            pc.greater(minute, pa.scalar(59, pa.int64())),
            pc.greater(second, pa.scalar(59, pa.int64())),
        ),
    )
    if pc.any(invalid_clock).as_py():
        raise ValueError(f"{source_name} contains an invalid wall-clock time")

    seconds_since_midnight = pc.add(
        pc.add(
            pc.multiply(hour, pa.scalar(3_600, pa.int64())),
            pc.multiply(minute, pa.scalar(60, pa.int64())),
        ),
        second,
    )
    offset_us = pc.add(
        pc.multiply(
            seconds_since_midnight,
            pa.scalar(1_000_000, pa.int64()),
        ),
        pc.multiply(millisecond, pa.scalar(1_000, pa.int64())),
    )
    midnight_utc_us = DateTimeUtils.local_time_to_utc_epoch_us(
        time(0, 0),
        date.fromisoformat(trade_date),
    )
    return pc.add(pa.scalar(midnight_utc_us, pa.int64()), offset_us)


def _map_values_or_null(
    values: pa.Array | pa.ChunkedArray,
    mapping: Mapping[str, str],
) -> pa.Array | pa.ChunkedArray:
    keys = pa.array(list(mapping.keys()))
    mapped_values = pa.array(list(mapping.values()))
    indexes = pc.index_in(values, keys)
    miss = pc.less(indexes, 0)
    safe_indexes = pc.if_else(
        miss,
        pa.nulls(len(values), type=pa.int32()),
        indexes,
    )
    return pc.take(mapped_values, safe_indexes)
