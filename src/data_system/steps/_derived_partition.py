# filepath: src/data_system/steps/_derived_partition.py
"""Shared publication boundary for derived daily partitions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pyarrow as pa

from src.access import meta
from src.utils import table_ops
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


def _publish_derived_partition(
    *,
    pm: PathManager,
    meta_path: Path,
    output_path: Path,
    build: Callable[[], pa.Table],
    who: str,
) -> int | None:
    if (
        meta.find(
            pm=pm,
            meta_path=meta_path,
            expected_payload_path=output_path,
        )
        is not None
    ):
        return None

    table = build()
    table_ops.require_nonempty(table, who=who)
    write_parquet_atomic(output_file=output_path, table=table)
    meta.commit(pm=pm, payload_path=output_path)
    return table.num_rows
