# filepath: src/data_system/normalize/__init__.py
"""Shared contracts for raw-to-processed normalization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

import pyarrow as pa


@dataclass(frozen=True, slots=True)
class NormalizeOutput:
    """Return one normalized table and its optional symbol slices.

    Example:
        output = NormalizeOutput(table=pa.table({"value": [1]}))
    """

    table: pa.Table
    symbol_slices: Mapping[str, range] | None = None

    def __post_init__(self) -> None:
        if self.symbol_slices is not None:
            object.__setattr__(
                self,
                "symbol_slices",
                MappingProxyType(dict(self.symbol_slices)),
            )


class NormalizeOperation(Protocol):
    """Convert one committed raw payload into one processed table.

    Example:
        from src.data_system.normalize.tushare import normalize_tushare

        operation: NormalizeOperation = normalize_tushare
        output = operation(
            input_file=Path("/data/raw.parquet"),
            output_name=Path("/data/processed.parquet"),
            raw_object="daily_bar",
            trade_date="2026-07-27",
            target_name="daily_bar",
        )
    """

    def __call__(
        self,
        *,
        input_file: Path,
        output_name: Path,
        raw_object: str,
        trade_date: str,
        target_name: str,
    ) -> NormalizeOutput:
        """Normalize one payload using its complete source identity.

        Example:
            output = operation(
                input_file=Path("/data/raw.parquet"),
                output_name=Path("/data/processed.parquet"),
                raw_object="daily_bar",
                trade_date="2026-07-27",
                target_name="daily_bar",
            )
        """
        ...
