# filepath: src/data_system/normalize/engine.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

import pyarrow as pa
import pyarrow.compute as pc


@dataclass(frozen=True, slots=True)
class Level2EventSpec:
    """Admitted route from ``docs/data/level2_normalization.md``."""

    raw_object: str
    output: str
    exchange: str
    kind: Literal["trade"]


LEVEL2_EVENT_SPECS: Mapping[
    tuple[str, str],
    Level2EventSpec,
] = MappingProxyType(
    {
        ("SH_Stock_OrderTrade", "sh_trade"): Level2EventSpec(
            raw_object="SH_Stock_OrderTrade",
            output="sh_trade",
            exchange="sh",
            kind="trade",
        ),
        ("SZ_Trade", "sz_trade"): Level2EventSpec(
            raw_object="SZ_Trade",
            output="sz_trade",
            exchange="sz",
            kind="trade",
        ),
    }
)


def resolve_level2_event_spec(*, raw_object: str, output: str) -> Level2EventSpec:
    """Return the admitted Level-2 route for a configured normalize task."""
    try:
        return LEVEL2_EVENT_SPECS[(raw_object, output)]
    except KeyError as exc:
        raise ValueError(
            "unsupported Level-2 raw/output normalize target: "
            f"raw_object={raw_object!r}, output={output!r}"
        ) from exc


def filter_trade_only(table: pa.Table) -> pa.Table:
    """
    严格的 TRADE 语义过滤（仅供 trade pipeline 使用）

    保证输出满足：
      - event == "TRADE"
      - price > 0
      - volume > 0
    """

    is_trade = pc.equal(table["event"], pa.scalar("TRADE", type=pa.string()))

    price_positive = pc.greater(table["price"], pa.scalar(0))

    volume_positive = pc.greater(table["volume"], pa.scalar(0))

    # 🔒 Arrow-safe logical AND（必须用 pc.and_）
    mask = pc.and_(
        pc.and_(is_trade, price_positive),
        volume_positive,
    )

    return table.filter(mask)
