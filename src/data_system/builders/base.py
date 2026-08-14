# filepath: src/data_system/builders/base.py
"""Builder protocols for formal feature and label datasets."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import pyarrow as pa

from src.access import Access
from src.utils.path import PathManager


class FeatureBuilder(Protocol):
    """Build one formal feature partition from its required input.

    Example:
        table = builder.read_input(
            access=access,
            pm=path_manager,
            processed_version="v1",
            trade_date="2026-07-20",
        )
        features = builder.build_partition(table)
    """

    key_columns: tuple[str, ...]
    output_columns: tuple[str, ...]

    def read_input(
        self,
        *,
        access: Access,
        pm: PathManager,
        processed_version: str,
        trade_date: str,
    ) -> pa.Table:
        """Read the complete formal input for one feature partition.

        Example:
            table = builder.read_input(
                access=access,
                pm=path_manager,
                processed_version="v1",
                trade_date="2026-07-20",
            )
        """
        ...

    def build_partition(self, table: pa.Table) -> pa.Table: ...


class LabelBuilder(Protocol):
    """Build one formal label partition from its required input.

    Example:
        table = builder.read_input(
            access=access,
            pm=path_manager,
            processed_version="v1",
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
        access: Access,
        pm: PathManager,
        processed_version: str,
        trade_dates: Sequence[str],
    ) -> pa.Table:
        """Read the complete formal input window supplied by the workflow.

        Example:
            table = builder.read_input(
                access=access,
                pm=path_manager,
                processed_version="v1",
                trade_dates=("2026-07-16", "2026-07-17", "2026-07-20"),
            )
        """
        ...

    def build_partition(
        self,
        table: pa.Table,
    ) -> pa.Table: ...
