# filepath: src/data_system/market_phase.py
from __future__ import annotations

from enum import IntEnum


class MarketPhase(IntEnum):
    """Identify the execution mechanism of an intraday market fact.

    Codes are owned by ``docs/data/market_phase.md``.

    Example:
        phase = MarketPhase.CONTINUOUS
        code = int(phase)
    """

    AUCTION = 0
    BREAK = 1
    CONTINUOUS = 2
    FIXED_PRICE = 3
