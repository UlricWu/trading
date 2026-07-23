# filepath: src/data_system/normalize/parser_engine.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import pyarrow as pa
import pyarrow.compute as pc

from src.data_system.arrow.ops import map_values_or_null, zeros_i64
from src.utils.datetime_utils import DateTimeUtils

INTERNAL_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        # 🔒 唯一时间轴
        ("ts_utc", pa.int64()),
        ("event", pa.string()),
        ("order_id", pa.int64()),
        ("side", pa.string()),
        ("price", pa.float64()),
        ("volume", pa.int64()),
        ("buy_no", pa.int64()),
        ("sell_no", pa.int64()),
    ]
)


# =============================================================================
# Exchange Definition
# =============================================================================


@dataclass(frozen=True, slots=True)
class ExchangeDefinition:
    """Define source fields and value mappings for one exchange event kind."""

    symbol_field: str
    time_field: str
    event_field: str
    event_mapping: Mapping[str, str]
    price_field: str
    volume_field: str
    side_field: str | None
    side_mapping: Mapping[str, str] | None
    id_field: str
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


# =============================================================================
# Exchange registry
# =============================================================================


EXCHANGE_REGISTRY: Mapping[
    str,
    Mapping[str, ExchangeDefinition],
] = MappingProxyType(
    {
        # 上海
        "sh": MappingProxyType(
            {
                "order": ExchangeDefinition(
                    symbol_field="SecurityID",
                    time_field="TradeTime",
                    event_field="TickType",
                    event_mapping={"A": "ADD", "D": "CANCEL"},
                    price_field="Price",
                    volume_field="Volume",
                    side_field="Side",
                    side_mapping={"1": "B", "2": "S"},
                    id_field="SubSeq",
                    buy_no_field=None,
                    sell_no_field=None,
                ),
                "trade": ExchangeDefinition(
                    symbol_field="SecurityID",
                    time_field="TradeTime",
                    event_field="TickType",
                    event_mapping={"T": "TRADE"},
                    price_field="Price",
                    volume_field="Volume",
                    side_field="Side",
                    side_mapping={"1": "B", "2": "S"},
                    id_field="SubSeq",
                    buy_no_field="BuyNo",
                    sell_no_field="SellNo",
                ),
            }
        ),
        # 深圳
        "sz": MappingProxyType(
            {
                "order": ExchangeDefinition(
                    symbol_field="SecurityID",
                    time_field="TradeTime",
                    event_field="OrderType",
                    event_mapping={"0": "CANCEL", "1": "ADD", "2": "ADD", "3": "ADD"},
                    price_field="Price",
                    volume_field="Volume",
                    side_field="Side",
                    side_mapping={"1": "B", "2": "S"},
                    id_field="SubSeq",
                    buy_no_field=None,
                    sell_no_field=None,
                ),
                "trade": ExchangeDefinition(
                    symbol_field="SecurityID",
                    time_field="TradeTime",
                    event_field="ExecType",
                    event_mapping={"1": "TRADE", "2": "CANCEL"},
                    price_field="TradePrice",
                    volume_field="TradeVolume",
                    side_field=None,
                    side_mapping=None,
                    id_field="SubSeq",
                    buy_no_field="BuyNo",
                    sell_no_field="SellNo",
                ),
            }
        ),
    }
)

# =============================================================================
# Time utilities
# =============================================================================


def _trade_time_to_utc_epoch_us(
    values: pa.Array | pa.ChunkedArray,
) -> pa.Array | pa.ChunkedArray:
    """Convert strict market-local ``TradeTime`` values to UTC microseconds."""
    if not pa.types.is_string(values.type):
        raise TypeError("TradeTime must be a string column")
    if values.null_count:
        raise ValueError("TradeTime must not contain null values")

    valid_format = pc.match_substring_regex(
        values,
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{1,6}$",
    )
    if pc.any(pc.invert(valid_format)).as_py():
        raise ValueError(
            "TradeTime must use YYYY-MM-DD HH:MM:SS with 1-6 fractional digits"
        )

    split = pc.split_pattern(values, ".")
    second_values = pc.list_element(split, 0)
    fraction_values = pc.list_element(split, 1)
    options = pc.StrptimeOptions(
        format="%Y-%m-%d %H:%M:%S",
        unit="us",
    )

    try:
        local_seconds = pc.strptime(second_values, options=options)
        normalized_seconds = pc.utf8_slice_codeunits(
            pc.strftime(
                local_seconds,
                format="%Y-%m-%d %H:%M:%S",
            ),
            start=0,
            stop=19,
        )
        if pc.any(pc.not_equal(second_values, normalized_seconds)).as_py():
            raise ValueError("TradeTime contains an invalid calendar time")
        utc_seconds = pc.assume_timezone(
            local_seconds,
            DateTimeUtils.MARKET_TIMEZONE.key,
        )
    except pa.ArrowInvalid as exc:
        raise ValueError("TradeTime contains an invalid calendar time") from exc

    epoch_us = pc.cast(utc_seconds, pa.int64())
    fraction_us = pc.cast(
        pc.utf8_rpad(fraction_values, 6, "0"),
        pa.int64(),
    )
    return pc.add(epoch_us, fraction_us)


def parse_events_arrow(
    table: pa.Table,
    *,
    exchange: str,
    kind: str,
) -> pa.Table:
    """Parse one configured raw Level-2 batch into ``INTERNAL_SCHEMA``."""

    # ------------------------------------------------------------
    # Registry lookup
    # ------------------------------------------------------------
    try:
        definition = EXCHANGE_REGISTRY[exchange][kind]
    except KeyError as exc:
        raise ValueError(
            f"unsupported Level-2 parser route: exchange={exchange}, kind={kind}"
        ) from exc

    # ------------------------------------------------------------
    # Empty table → empty but schema-frozen output
    # ------------------------------------------------------------
    if table.num_rows == 0:
        return pa.table(
            {field.name: pa.array([], type=field.type) for field in INTERNAL_SCHEMA}
        )

    if definition.time_field not in table.column_names:
        raise ValueError(f"{definition.time_field} is required")

    ts_utc = _trade_time_to_utc_epoch_us(
        table[definition.time_field],
    )

    # ------------------------------------------------------------
    # Event / side mapping
    # ------------------------------------------------------------
    event = map_values_or_null(
        table[definition.event_field],
        definition.event_mapping,
    )

    if definition.side_field and definition.side_mapping:
        side = map_values_or_null(
            table[definition.side_field],
            definition.side_mapping,
        )
    else:
        side = pa.nulls(table.num_rows)

    # ------------------------------------------------------------
    # Buy / sell no
    # ------------------------------------------------------------
    buy_no = (
        table[definition.buy_no_field]
        if definition.buy_no_field
        else zeros_i64(table.num_rows)
    )
    sell_no = (
        table[definition.sell_no_field]
        if definition.sell_no_field
        else zeros_i64(table.num_rows)
    )

    # ------------------------------------------------------------
    # Build output
    # ------------------------------------------------------------
    out = pa.table(
        {
            "symbol": table[definition.symbol_field],
            "ts_utc": ts_utc,
            "event": event,
            "order_id": pc.cast(table[definition.id_field], pa.int64()),
            "side": side,
            "price": pc.cast(table[definition.price_field], pa.float64()),
            "volume": pc.cast(table[definition.volume_field], pa.int64()),
            "buy_no": pc.cast(buy_no, pa.int64()),
            "sell_no": pc.cast(sell_no, pa.int64()),
        }
    )
    out = out.cast(INTERNAL_SCHEMA)

    return out
