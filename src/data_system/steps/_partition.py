# filepath: src/data_system/steps/_partition.py
"""Share Parquet publication and calendar, feature, and label reuse policies."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pyarrow as pa

from src.access import meta
from src.utils import table_ops
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import ObjectPaths, PathManager


def _publish_partition(
    *,
    pm: PathManager,
    paths: ObjectPaths,
    build: Callable[[], pa.Table],
    who: str,
    upstream_meta_path: Path | None = None,
) -> int | None:
    """Reuse valid Meta, or build and publish a nonempty partition.

    Return published rows, or ``None`` for reuse; the concrete Step logs that
    result. The callable is consumed synchronously only on a Meta miss.
    """
    existing = meta.find(
        pm=pm,
        meta_path=paths.meta_path,
        expected_payload_path=paths.payload_path,
    )
    if existing is not None:
        if existing.symbol_slices is not None:
            raise RuntimeError(
                f"partition Meta must not contain symbol_slices: "
                f"meta_path={paths.meta_path}"
            )
        if upstream_meta_path is None and existing.upstream is not None:
            raise RuntimeError(
                f"partition Meta must not contain upstream: meta_path={paths.meta_path}"
            )
        return None

    table = build()
    table_ops.require_nonempty(table, who=who)
    _publish_parquet_object(
        pm=pm,
        paths=paths,
        table=table,
        upstream_meta_path=upstream_meta_path,
    )
    return table.num_rows


def _publish_parquet_object(
    *,
    pm: PathManager,
    paths: ObjectPaths,
    table: pa.Table,
    upstream_meta_path: Path | None = None,
    symbol_slices: Mapping[str, range] | None = None,
) -> None:
    write_parquet_atomic(output_file=paths.payload_path, table=table)
    meta.commit(
        pm=pm,
        payload_path=paths.payload_path,
        upstream_meta_path=upstream_meta_path,
        symbol_slices=symbol_slices,
    )
