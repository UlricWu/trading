# filepath: src/data_system/builders/level2_stock_trade_1m.py
"""Build sparse phase-aware stock minute facts from formal Level-2 trades."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc

from src.utils import table_ops
from src.utils.datetime_utils import DateTimeUtils

__all__ = ("build_level2_stock_trade_1m",)

_MINUTE_FACT_KEY = (
    "symbol",
    "trade_date",
    "minute_start_ts_utc",
    "phase",
)
_OUTPUT_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("trade_date", pa.string(), nullable=False),
        pa.field("minute_start_ts_utc", pa.int64(), nullable=False),
        pa.field("phase", pa.int8(), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume_sum", pa.int64(), nullable=False),
        pa.field("notional_sum", pa.float64(), nullable=False),
        pa.field("trade_count", pa.int64(), nullable=False),
        pa.field("tick_signed_volume_sum", pa.int64(), nullable=False),
        pa.field("tick_signed_notional_sum", pa.float64(), nullable=False),
    ]
)
_INPUT_COLUMNS = (
    "symbol",
    "ts_utc",
    "main_seq",
    "sub_seq",
    "price",
    "volume",
    "security_type",
    "phase",
    "notional",
    "trade_side",
)
_INPUT_NUMERIC_COLUMNS = (
    "ts_utc",
    "main_seq",
    "sub_seq",
    "price",
    "volume",
    "phase",
    "notional",
    "trade_side",
)
_SORT_COLUMNS = (
    "symbol",
    "ts_utc",
    "main_seq",
    "sub_seq",
)


def build_level2_stock_trade_1m(
    trades: pa.Table,
    *,
    trade_date: str,
) -> pa.Table:
    """Aggregate one bounded Level-2 trade batch into sparse minute facts.

    The input may contain multiple security types. Only rows whose persisted
    ``security_type`` is ``stock`` enter the result.

    Example:
        minute_facts = build_level2_stock_trade_1m(
            pa.table(
                {
                    "symbol": ["600000"],
                    "ts_utc": pa.array([1_767_066_600_000_000], pa.int64()),
                    "main_seq": pa.array([1], pa.int64()),
                    "sub_seq": pa.array([1], pa.int64()),
                    "price": [10.0],
                    "volume": pa.array([100], pa.int64()),
                    "security_type": ["stock"],
                    "phase": pa.array([2], pa.int8()),
                    "notional": [1_000.0],
                    "trade_side": pa.array([0], pa.int8()),
                }
            ),
            trade_date="2026-01-01",
        )
    """
    if not isinstance(trades, pa.Table):
        raise TypeError("trades must be a pyarrow.Table")
    validated_trade_date = DateTimeUtils.require_system_date(
        trade_date,
        field_name="trade_date",
    )
    table_ops.require_columns(trades, _INPUT_COLUMNS, who="Level2 stock minute")

    stock_trades = trades.filter(
        pc.equal(trades.column("security_type"), pa.scalar("stock"))
    )
    if stock_trades.num_rows == 0:
        return pa.Table.from_batches([], schema=_OUTPUT_SCHEMA)

    table_ops.require_nonempty_strings(
        stock_trades,
        ("symbol",),
        who="Level2 stock minute",
    )
    table_ops.require_finite(
        stock_trades,
        _INPUT_NUMERIC_COLUMNS,
        who="Level2 stock minute",
    )
    ordered_trades = stock_trades.sort_by(
        [(column, "ascending") for column in _SORT_COLUMNS]
    )

    if ordered_trades.num_rows > 1:
        same_order_identity = pc.equal(
            ordered_trades.column("symbol").slice(1),
            ordered_trades.column("symbol").slice(0, ordered_trades.num_rows - 1),
        )
        for column in _SORT_COLUMNS[1:]:
            same_order_identity = pc.and_(
                same_order_identity,
                pc.equal(
                    ordered_trades.column(column).slice(1),
                    ordered_trades.column(column).slice(
                        0,
                        ordered_trades.num_rows - 1,
                    ),
                ),
            )
        conflicting_price = pc.and_(
            same_order_identity,
            pc.not_equal(
                ordered_trades.column("price").slice(1),
                ordered_trades.column("price").slice(
                    0,
                    ordered_trades.num_rows - 1,
                ),
            ),
        )
        if pc.any(conflicting_price).as_py() is True:
            raise ValueError(
                "Level2 stock minute: identical order identity has "
                "conflicting prices"
            )

    minute_start_ts_utc = pc.cast(
        pc.floor_temporal(
            pc.cast(ordered_trades.column("ts_utc"), pa.timestamp("us", tz="UTC")),
            unit="minute",
        ),
        pa.int64(),
    )
    volume = pc.cast(ordered_trades.column("volume"), pa.int64(), safe=True)
    trade_side_int64 = pc.cast(
        ordered_trades.column("trade_side"),
        pa.int64(),
        safe=True,
    )
    signed_volume = pc.multiply_checked(volume, trade_side_int64)
    notional = pc.cast(
        ordered_trades.column("notional"),
        pa.float64(),
        safe=True,
    )
    signed_notional = pc.multiply_checked(
        notional,
        pc.cast(trade_side_int64, pa.float64()),
    )
    grouped = pa.table(
        {
            "symbol": ordered_trades.column("symbol"),
            "trade_date": pa.repeat(
                pa.scalar(validated_trade_date, type=pa.string()),
                ordered_trades.num_rows,
            ),
            "minute_start_ts_utc": minute_start_ts_utc,
            "phase": ordered_trades.column("phase"),
            "price": ordered_trades.column("price"),
            "volume": pc.cast(volume, pa.decimal128(38, 0), safe=True),
            "notional": notional,
            "signed_volume": pc.cast(
                signed_volume,
                pa.decimal128(38, 0),
                safe=True,
            ),
            "signed_notional": signed_notional,
        }
    ).group_by(list(_MINUTE_FACT_KEY), use_threads=False).aggregate(
        [
            ("price", "first"),
            ("price", "max"),
            ("price", "min"),
            ("price", "last"),
            ("volume", "sum"),
            ("notional", "sum"),
            ("price", "count"),
            ("signed_volume", "sum"),
            ("signed_notional", "sum"),
        ]
    )

    output = pa.Table.from_arrays(
        [
            pc.cast(grouped.column("symbol"), pa.string(), safe=True),
            pc.cast(grouped.column("trade_date"), pa.string(), safe=True),
            pc.cast(
                grouped.column("minute_start_ts_utc"),
                pa.int64(),
                safe=True,
            ),
            pc.cast(grouped.column("phase"), pa.int8(), safe=True),
            pc.cast(grouped.column("price_first"), pa.float64(), safe=True),
            pc.cast(grouped.column("price_max"), pa.float64(), safe=True),
            pc.cast(grouped.column("price_min"), pa.float64(), safe=True),
            pc.cast(grouped.column("price_last"), pa.float64(), safe=True),
            pc.cast(grouped.column("volume_sum"), pa.int64(), safe=True),
            pc.cast(grouped.column("notional_sum"), pa.float64(), safe=True),
            pc.cast(grouped.column("price_count"), pa.int64(), safe=True),
            pc.cast(
                grouped.column("signed_volume_sum"),
                pa.int64(),
                safe=True,
            ),
            pc.cast(
                grouped.column("signed_notional_sum"),
                pa.float64(),
                safe=True,
            ),
        ],
        schema=_OUTPUT_SCHEMA,
    ).sort_by([(column, "ascending") for column in _MINUTE_FACT_KEY])
    table_ops.require_finite(
        output,
        (
            "minute_start_ts_utc",
            "phase",
            "open",
            "high",
            "low",
            "close",
            "volume_sum",
            "notional_sum",
            "trade_count",
            "tick_signed_volume_sum",
            "tick_signed_notional_sum",
        ),
        who="Level2 stock minute output",
    )
    return output
