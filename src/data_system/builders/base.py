# filepath: src/data_system/builders/base.py
"""Builder protocols for formal feature and label datasets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import pyarrow as pa

from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class InputSpec:
    logical_dataset: str
    version: str


class FeatureBuilder(Protocol):
    """Build one formal feature partition from its required input."""

    key_columns: tuple[str, ...]
    output_columns: tuple[str, ...]

    def read_input(self, *, pm: PathManager, trade_date: str) -> pa.Table:
        ...

    def build_partition(self, table: pa.Table) -> pa.Table:
        ...


class LabelBuilder(Protocol):
    """Build one formal label partition from its required input."""

    key_columns: tuple[str, ...]
    output_columns: tuple[str, ...]

    def target_lookahead(self, label_column: str) -> int:
        ...

    def read_input(
        self,
        *,
        pm: PathManager,
        trade_date: str,
    ) -> pa.Table | Mapping[InputSpec, pa.Table]:
        ...

    def build_partition(
        self,
        table: pa.Table | Mapping[InputSpec, pa.Table],
    ) -> pa.Table:
        ...
