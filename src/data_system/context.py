# filepath: src/data_system/context.py
from __future__ import annotations

from dataclasses import dataclass

from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class DataContext:
    """Carry the immutable identity of one trade-date data workflow.

    Example:
        context = DataContext(
            trade_date="2026-07-20",
            pm=path_manager,
        )
    """

    trade_date: str
    pm: PathManager
