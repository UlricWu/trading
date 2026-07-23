# filepath: src/data_system/normalize/security_type_resolver.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pyarrow as pa
import pyarrow.compute as pc

from src import logs
from src.data_system.arrow.ops import append_or_replace


class SecurityType(str, Enum):
    STOCK = "stock"
    FUND = "fund"
    ETF = "etf"
    BOND = "bond"
    CONVERTIBLE_BOND = "convertible_bond"
    BOND_REPO = "bond_repo"
    B_SHARE = "b_share"
    CDR = "cdr"
    ALL = 'all'


@dataclass(frozen=True)
class SecurityTypeRange:
    exchange: str
    start: int
    end: int
    security_type: SecurityType | None
    reason: str | None = None


_SH_UNSUPPORTED = "SH code segment has no defined Level-2 trade phase"
_SZ_UNSUPPORTED = "SZ code segment has no defined Level-2 trade phase"

SECURITY_TYPE_RANGES: tuple[SecurityTypeRange, ...] = (
    # SH stock / CDR / B share
    SecurityTypeRange("sh", 600000, 600999, SecurityType.STOCK),
    SecurityTypeRange("sh", 601000, 601999, SecurityType.STOCK),
    SecurityTypeRange("sh", 603000, 603999, SecurityType.STOCK),
    SecurityTypeRange("sh", 605000, 605999, SecurityType.STOCK),
    SecurityTypeRange("sh", 688000, 688999, SecurityType.STOCK),
    SecurityTypeRange("sh", 689000, 689999, SecurityType.CDR),
    SecurityTypeRange("sh", 900000, 900999, SecurityType.B_SHARE),

    # SH fund
    SecurityTypeRange("sh", 500000, 500999, SecurityType.FUND),
    SecurityTypeRange("sh", 501000, 501999, SecurityType.FUND),
    SecurityTypeRange("sh", 502000, 502999, SecurityType.FUND),
    SecurityTypeRange("sh", 505800, 505899, SecurityType.FUND),
    SecurityTypeRange("sh", 506000, 506099, SecurityType.FUND),
    SecurityTypeRange("sh", 508000, 508999, SecurityType.FUND),
    SecurityTypeRange("sh", 519000, 519999, SecurityType.FUND),
    SecurityTypeRange("sh", 550000, 550999, SecurityType.FUND),

    # SH ETF
    SecurityTypeRange("sh", 510000, 510999, SecurityType.ETF),
    SecurityTypeRange("sh", 511000, 511999, SecurityType.ETF),
    SecurityTypeRange("sh", 512000, 512999, SecurityType.ETF),
    SecurityTypeRange("sh", 513000, 513999, SecurityType.ETF),
    SecurityTypeRange("sh", 515000, 515999, SecurityType.ETF),
    SecurityTypeRange("sh", 516000, 516999, SecurityType.ETF),
    SecurityTypeRange("sh", 517000, 517999, SecurityType.ETF),
    SecurityTypeRange("sh", 518000, 518999, SecurityType.ETF),
    SecurityTypeRange("sh", 520000, 520999, SecurityType.ETF),
    SecurityTypeRange("sh", 526000, 526999, SecurityType.ETF),
    SecurityTypeRange("sh", 530000, 530999, SecurityType.ETF),
    SecurityTypeRange("sh", 551000, 551999, SecurityType.ETF),
    SecurityTypeRange("sh", 560000, 560999, SecurityType.ETF),
    SecurityTypeRange("sh", 561000, 561999, SecurityType.ETF),
    SecurityTypeRange("sh", 562000, 562999, SecurityType.ETF),
    SecurityTypeRange("sh", 563000, 563999, SecurityType.ETF),
    SecurityTypeRange("sh", 588000, 588999, SecurityType.ETF),
    SecurityTypeRange("sh", 589000, 589999, SecurityType.ETF),

    # SH convertible bond / repo / bond
    SecurityTypeRange("sh", 100000, 100899, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sh", 110000, 110999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sh", 111000, 111499, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sh", 113000, 113999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sh", 118000, 118499, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sh", 126000, 126999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sh", 132000, 132999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sh", 137000, 137499, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sh", 201000, 207999, SecurityType.BOND_REPO),

    SecurityTypeRange("sh", 9000, 10999, SecurityType.BOND),
    SecurityTypeRange("sh", 18000, 20999, SecurityType.BOND),
    SecurityTypeRange("sh", 101000, 101999, SecurityType.BOND),
    SecurityTypeRange("sh", 109000, 109999, SecurityType.BOND),
    SecurityTypeRange("sh", 112000, 112999, SecurityType.BOND),
    SecurityTypeRange("sh", 114000, 115999, SecurityType.BOND),
    SecurityTypeRange("sh", 120000, 125999, SecurityType.BOND),
    SecurityTypeRange("sh", 127000, 131999, SecurityType.BOND),
    SecurityTypeRange("sh", 135000, 136999, SecurityType.BOND),
    SecurityTypeRange("sh", 137500, 137999, SecurityType.BOND),
    SecurityTypeRange("sh", 138500, 138999, SecurityType.BOND),
    SecurityTypeRange("sh", 139000, 140999, SecurityType.BOND),
    SecurityTypeRange("sh", 142000, 143999, SecurityType.BOND),
    SecurityTypeRange("sh", 145000, 145999, SecurityType.BOND),
    SecurityTypeRange("sh", 149000, 152999, SecurityType.BOND),
    SecurityTypeRange("sh", 155000, 157999, SecurityType.BOND),
    SecurityTypeRange("sh", 159000, 160999, SecurityType.BOND),
    SecurityTypeRange("sh", 162000, 169999, SecurityType.BOND),
    SecurityTypeRange("sh", 171000, 171999, SecurityType.BOND),
    SecurityTypeRange("sh", 173000, 173999, SecurityType.BOND),
    SecurityTypeRange("sh", 175000, 180999, SecurityType.BOND),
    SecurityTypeRange("sh", 182300, 182999, SecurityType.BOND),
    SecurityTypeRange("sh", 183000, 186999, SecurityType.BOND),
    SecurityTypeRange("sh", 188000, 189999, SecurityType.BOND),
    SecurityTypeRange("sh", 194000, 194999, SecurityType.BOND),
    SecurityTypeRange("sh", 196000, 199999, SecurityType.BOND),
    SecurityTypeRange("sh", 230000, 238999, SecurityType.BOND),
    SecurityTypeRange("sh", 240000, 247999, SecurityType.BOND),
    SecurityTypeRange("sh", 250000, 254999, SecurityType.BOND),
    SecurityTypeRange("sh", 258000, 259999, SecurityType.BOND),
    SecurityTypeRange("sh", 260000, 266999, SecurityType.BOND),
    SecurityTypeRange("sh", 270000, 272999, SecurityType.BOND),
    SecurityTypeRange("sh", 280000, 283999, SecurityType.BOND),

    # SH unsupported
    SecurityTypeRange("sh", 133000, 134999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 141000, 141999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 144000, 144999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 153000, 154999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 158000, 158999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 161000, 161999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 170000, 170999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 172000, 172999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 174000, 174999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 181000, 181999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 182000, 182299, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 187000, 187999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 190000, 193999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 195000, 195999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 330000, 330999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 360000, 360999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 580000, 580999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 582000, 582999, None, _SH_UNSUPPORTED),
    SecurityTypeRange("sh", 700000, 899999, None, _SH_UNSUPPORTED),

    # SZ supported
    SecurityTypeRange("sz", 0, 999, SecurityType.STOCK),
    SecurityTypeRange("sz", 1200, 1999, SecurityType.STOCK),
    SecurityTypeRange("sz", 2000, 4999, SecurityType.STOCK),
    SecurityTypeRange("sz", 300000, 309799, SecurityType.STOCK),
    SecurityTypeRange("sz", 1001, 1199, SecurityType.CDR),
    SecurityTypeRange("sz", 309800, 309999, SecurityType.CDR),
    SecurityTypeRange("sz", 200000, 209999, SecurityType.B_SHARE),
    SecurityTypeRange("sz", 119400, 119499, SecurityType.FUND),
    SecurityTypeRange("sz", 121500, 121999, SecurityType.FUND),
    SecurityTypeRange("sz", 150000, 151999, SecurityType.FUND),
    SecurityTypeRange("sz", 160000, 179999, SecurityType.FUND),
    SecurityTypeRange("sz", 180101, 180999, SecurityType.FUND),
    SecurityTypeRange("sz", 184000, 184999, SecurityType.FUND),
    SecurityTypeRange("sz", 158000, 159999, SecurityType.ETF),
    SecurityTypeRange("sz", 115000, 115099, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sz", 115600, 115999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sz", 117000, 117499, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sz", 120000, 120999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sz", 121000, 121499, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sz", 123000, 123999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sz", 124000, 124999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sz", 127000, 127999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sz", 128000, 128999, SecurityType.CONVERTIBLE_BOND),
    SecurityTypeRange("sz", 131800, 132099, SecurityType.BOND_REPO),
    SecurityTypeRange("sz", 100000, 114999, SecurityType.BOND),
    SecurityTypeRange("sz", 115100, 115599, SecurityType.BOND),
    SecurityTypeRange("sz", 116000, 116999, SecurityType.BOND),
    SecurityTypeRange("sz", 117500, 117999, SecurityType.BOND),
    SecurityTypeRange("sz", 118000, 118999, SecurityType.BOND),
    SecurityTypeRange("sz", 119000, 119399, SecurityType.BOND),
    SecurityTypeRange("sz", 119500, 119999, SecurityType.BOND),
    SecurityTypeRange("sz", 130000, 130999, SecurityType.BOND),
    SecurityTypeRange("sz", 133000, 139999, SecurityType.BOND),
    SecurityTypeRange("sz", 143000, 149999, SecurityType.BOND),
    SecurityTypeRange("sz", 189500, 189999, SecurityType.BOND),
    SecurityTypeRange("sz", 190000, 199999, SecurityType.BOND),
    SecurityTypeRange("sz", 500000, 529999, SecurityType.BOND),
    SecurityTypeRange("sz", 560000, 599999, SecurityType.BOND),

    # SZ unsupported
    SecurityTypeRange("sz", 30000, 39999, None, _SZ_UNSUPPORTED),
    SecurityTypeRange("sz", 70000, 84999, None, _SZ_UNSUPPORTED),
    SecurityTypeRange("sz", 220000, 299999, None, _SZ_UNSUPPORTED),
    SecurityTypeRange("sz", 350000, 399999, None, _SZ_UNSUPPORTED),
    SecurityTypeRange("sz", 970000, 989999, None, _SZ_UNSUPPORTED),
)


def execute(
        table: pa.Table,
        exchange: str,
        col: str = "security_type",
) -> pa.Table:
    try:
        symbol_int = pc.cast(table['symbol'], pa.int32())
    except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
        raise ValueError("invalid SecurityID") from exc

    invalid = pc.or_(
        pc.is_null(symbol_int),
        pc.or_(
            pc.less(symbol_int, pa.scalar(0, pa.int32())),
            pc.greater(symbol_int, pa.scalar(999999, pa.int32())),
        ),
    )
    invalid = pc.fill_null(invalid, True)
    if pc.any(invalid).as_py():
        raise ValueError("invalid SecurityID")

    security_type = pa.nulls(len(symbol_int), type=pa.string())
    matched = pa.repeat(pa.scalar(False), len(symbol_int))
    exchange_ranges = [r for r in SECURITY_TYPE_RANGES if r.exchange == exchange and r.reason is None]
    unsupported_ranges = [r for r in SECURITY_TYPE_RANGES if r.exchange == exchange and r.reason is not None]

    if not exchange_ranges and not unsupported_ranges:
        logs.warning(
            f"[SecurityTypeResolver] no_ranges exchange={exchange}"
        )
        return append_or_replace(table, col, security_type)

    for rule in exchange_ranges:
        start_number = pa.scalar(rule.start, pa.int32())
        end_number = pa.scalar(rule.end, pa.int32())
        mask = pc.and_(pc.greater_equal(symbol_int, start_number),
                       pc.less_equal(symbol_int, end_number))

        security_type = pc.if_else(
            mask,
            pa.scalar(rule.security_type.value, pa.string()),
            security_type,
        )
        matched = pc.or_(matched, mask)

    unsupported = pa.repeat(pa.scalar(False), len(symbol_int))
    for rule in unsupported_ranges:
        start_number = pa.scalar(rule.start, pa.int32())
        end_number = pa.scalar(rule.end, pa.int32())
        mask = pc.and_(pc.greater_equal(symbol_int, start_number),
                       pc.less_equal(symbol_int, end_number))
        unsupported = pc.or_(unsupported, mask)

    if pc.any(unsupported).as_py():
        raise ValueError("unsupported security_type segment")

    unmatched = pc.invert(pc.or_(matched, unsupported))
    if pc.any(unmatched).as_py():
        raise ValueError("unmatched security_type segment")

    return append_or_replace(table, col, security_type)


class SecurityTypeResolver:
    """Resolve normalized Level-2 security type column."""

    def resolve(
            self,
            *,
            table: pa.Table,
            exchange: str,
            trade_date: str,
            col: str = "security_type",
    ) -> pa.Table:
        _ = trade_date
        return execute(table=table, exchange=exchange, col=col)
