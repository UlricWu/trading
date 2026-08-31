# filepath: src/data_system/context.py
"""Range Context shared by one ordered offline data Step chain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DataContext:
    """Carry the requested range and resolved formal trade dates.

    Example:
        context = DataContext(start="2026-07-01", end="2026-07-20")
    """

    start: str
    end: str
    trade_dates: tuple[str, ...] = ()
