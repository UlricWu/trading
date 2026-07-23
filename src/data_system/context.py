# filepath: src/data_system/context.py
from __future__ import annotations

from dataclasses import dataclass

from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class DataContext:
    """Immutable execution identity for one trade-date data pipeline."""

    trade_date: str
    pm: PathManager
