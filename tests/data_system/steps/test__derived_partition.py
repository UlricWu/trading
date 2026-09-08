# filepath: tests/data_system/steps/test__derived_partition.py
"""Validate the derived partition publication boundary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import meta
from src.data_system.steps._derived_partition import _publish_derived_partition
from src.utils.path import PathManager


@pytest.mark.parametrize("relationship", ("upstream", "symbol_slices"))
def test_derived_partition_rejects_relationship_meta_without_overwriting(
    tmp_path: Path,
    relationship: str,
) -> None:
    pm = PathManager(tmp_path)
    paths = pm.feature_object(
        feature_set="tushare_daily_basic", version="v1", trade_date="2026-05-06"
    )
    paths.payload_path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"symbol": ["000001"]}), paths.payload_path)
    if relationship == "upstream":
        upstream = pm.processed_object(
            dataset_name="daily_bar", version="v1", trade_date="2026-05-06"
        )
        upstream.payload_path.parent.mkdir(parents=True)
        pq.write_table(pa.table({"symbol": ["000001"]}), upstream.payload_path)
        meta.commit(pm=pm, payload_path=upstream.payload_path)
        meta.commit(
            pm=pm,
            payload_path=paths.payload_path,
            upstream_meta_path=upstream.meta_path,
        )
    else:
        meta.commit(
            pm=pm,
            payload_path=paths.payload_path,
            symbol_slices={"000001": range(1)},
        )
    before = {
        path: (path.read_bytes(), path.stat())
        for path in (paths.payload_path, paths.meta_path)
    }
    builder = Mock(side_effect=AssertionError("invalid Meta must not rebuild"))

    with pytest.raises(RuntimeError, match=f"must not contain {relationship}"):
        _publish_derived_partition(
            pm=pm,
            meta_path=paths.meta_path,
            output_path=paths.payload_path,
            build=builder,
            who="feature; case=invalid_relationship",
        )

    builder.assert_not_called()
    for path, (contents, stat) in before.items():
        assert path.read_bytes() == contents
        assert path.stat().st_mtime_ns == stat.st_mtime_ns
        assert path.stat().st_ino == stat.st_ino
