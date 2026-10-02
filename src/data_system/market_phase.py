# filepath: src/data_system/market_phase.py

from __future__ import annotations

from enum import IntEnum


class MarketPhase(IntEnum):
    """Identify the execution mechanism of an intraday market fact.

    Codes, effective dates and source-native windows are owned by
    ``docs/data/market_phase.md``. ``CONTINUOUS`` includes continuous
    matching and the covered SZ fixed-income real-time executions without
    identifying their specific trading method. The current Level-2 producer
    emits only ``AUCTION`` and ``CONTINUOUS``; unsupported types or times fail.

    Example:
        phase = MarketPhase.CONTINUOUS
        code = int(phase)
    """

    AUCTION = 0
    BREAK = 1
    CONTINUOUS = 2
    FIXED_PRICE = 3
