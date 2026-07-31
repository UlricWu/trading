# filepath: src/data_system/builders/base.py
"""Builder protocols for formal feature and label datasets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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

    def read_input(self, *, pm: PathManager, trade_date: str) -> pa.Table: ...

    def build_partition(self, table: pa.Table) -> pa.Table: ...


class LabelBuilder(Protocol):
    """Build one formal label partition from its required input.

    Example:
        table = builder.read_input(
            pm=path_manager,
            trade_dates=("2026-07-16", "2026-07-17", "2026-07-20"),
        )
        labels = builder.build_partition(table)
    """

    key_columns: tuple[str, ...]
    output_columns: tuple[str, ...]

    def target_lookahead(self, label_column: str) -> int: ...

    def read_input(
        self,
        *,
        pm: PathManager,
        trade_dates: Sequence[str],
    ) -> pa.Table | Mapping[InputSpec, pa.Table]:
        """Read the complete formal input window supplied by the workflow.

        Example:
            table = builder.read_input(
                pm=path_manager,
                trade_dates=("2026-07-16", "2026-07-17", "2026-07-20"),
            )
        """
        ...

    def build_partition(
        self,
        table: pa.Table | Mapping[InputSpec, pa.Table],
    ) -> pa.Table: ...
