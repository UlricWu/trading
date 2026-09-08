# filepath: src/data_system/market_phase.py

from __future__ import annotations

from enum import IntEnum


class MarketPhase(IntEnum):
    """Identify the execution mechanism of an intraday market fact.

    The following rules are owned by ``docs/data/market_phase.md``. Times are
    Asia/Shanghai wall-clock; ``[a, b)`` excludes ``b`` and ``[a, b]``
    includes it.

    ``AUCTION = 0``:
        - SH stock/cdr from 2018-08-20:
          ``[09:25:00, 09:25:13)`` and ``[15:00:00, 15:00:03)``.
        - SH b_share on every supported date:
          ``[09:25:00, 09:25:13)`` and ``[15:00:00, 15:00:02)``.
        - SH fund/etf through 2026-07-05:
          ``[09:25:00, 09:25:13)``.
        - SH fund/etf from 2026-07-06:
          ``[09:25:00, 09:25:13)`` and ``[15:00:00, 15:00:03)``.
        - SH bond/convertible_bond/bond_repo on every supported date:
          ``[09:25:00, 09:25:13)``.
        - SZ stock on every supported date: ``[09:25:00, 09:25:01)``,
          ``[14:57:00, 14:57:01)``, and ``[15:00:00, 15:00:01)``.
        - SZ fund/etf/bond/b_share on every supported date and SZ convertible
          bond through 2020-06-07: ``[09:25:00, 09:25:01)`` and
          ``[15:00:00, 15:00:01)``.
        - SZ convertible_bond from 2020-06-08:
          ``[09:25:00, 09:25:01)``, ``[14:57:00, 14:57:01)``, and
          ``[15:00:00, 15:00:01)``.

    ``BREAK = 1``:
        Identifies no active execution mechanism only when a source-fact
        contract explicitly requires it. The current Level-2 producer never
        emits ``BREAK``; a trade outside a defined interval must fail instead.

    ``CONTINUOUS = 2``:
        - SH stock/cdr from 2018-08-20 and SH b_share on every supported date:
          ``[09:30:00, 11:30:00]`` and ``[13:00:00, 14:57:00)``.
        - SH fund/etf through 2026-07-05:
          ``[09:30:00, 11:30:00]`` and ``[13:00:00, 15:00:00)``.
        - SH fund/etf from 2026-07-06:
          ``[09:30:00, 11:30:00]`` and ``[13:00:00, 14:57:00)``.
        - SH bond/convertible_bond/bond_repo on every supported date:
          ``[09:30:00, 11:30:00]`` and ``[13:00:00, 15:30:00)``.
        - SZ stock/fund/etf/bond/convertible_bond/b_share on every supported
          date: ``[09:30:00, 11:30:00]`` and ``[13:00:00, 14:57:00)``.

    ``FIXED_PRICE = 3``:
        From 2026-07-06, the institutional after-hours fixed-price period for
        SH/SZ A-share stocks and ETFs is 15:05-15:30. The current
        ``SH_Stock_OrderTrade`` and ``SZ_Trade`` sources cannot identify these
        trades, so the producer must not infer or emit ``FIXED_PRICE``; the
        source-native 15:30 microsecond boundary remains undefined.

    SZ cdr and bond_repo have no defined trade phase. The current producer
    accepts positive trades only from ``SH_Stock_OrderTrade -> sh_trade`` and
    ``SZ_Trade -> sz_trade``; unsupported security types must fail.

    Example:
        phase = MarketPhase.CONTINUOUS
        code = int(phase)
    """

    AUCTION = 0
    BREAK = 1
    CONTINUOUS = 2
    FIXED_PRICE = 3
