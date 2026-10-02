# filepath: tests/data_system/test_level2_cache.py
"""Cache cleanup requires finished facts, matching cloud raw and exclusive access."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.access import meta
from src.config.data_config import SourceConfig
from src.data_system.brokers.level2 import Level2Broker
from src.data_system.level2_cache import level2_cache_lock, prune_level2_cache
from src.utils.path import PathManager


def test_cleanup_keeps_recent_and_incomplete_dates_and_checks_cloud(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    source = SourceConfig(enabled=True, broker="level2_ftp", group="offline_level2", raw_object="SZ_Trade", outputs=["sz_trade"])
    broker = Mock(spec=Level2Broker)
    archives = {}
    caches = {}
    for day in ("18", "21", "22", "23"):
        date = f"2026-09-{day}"
        archive = meta.RemoteRawRecord("SZ_Trade.csv.7z", 3, f"/level2/{date}/SZ_Trade.csv.7z")
        archives[archive.remote_path] = archive
        raw_meta = pm.raw_meta(broker="level2_ftp", source_name="sz_trade", trade_date=date)
        meta.commit_remote_raw(pm=pm, meta_path=raw_meta, record=archive)
        cache = pm.staging_payload(broker="level2_ftp", source_name="sz_trade", trade_date=date, payload_file=archive.payload)
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"raw")
        caches[day] = cache
        if day != "23":
            paths = pm.processed_object(dataset_name="sz_trade", version="v1", trade_date=date)
            paths.payload_path.parent.mkdir(parents=True)
            paths.payload_path.write_bytes(b"processed")
            meta.commit(pm=pm, payload_path=paths.payload_path, upstream_meta_path=raw_meta, symbol_slices={"000001": range(1)})
    broker.describe.side_effect = lambda path: archives[path] if "09-21" in path else None
    partial = caches["21"].parent / ".download" / "partial"
    partial.parent.mkdir()
    partial.write_bytes(b"partial")
    with level2_cache_lock(pm):
        assert prune_level2_cache(pm=pm, sources={"sz_trade": source}, processed_version="v1", keep_days=1, get_broker=lambda: broker) == 1
    assert caches["18"].exists()  # Cloud copy cannot be confirmed.
    assert not caches["21"].exists()
    assert caches["22"].exists()  # Latest successful session.
    assert caches["23"].exists()  # Failed or incomplete ingest.
    assert partial.read_bytes() == b"partial"
    meta.require(pm=pm, meta_path=pm.processed_object(dataset_name="sz_trade", version="v1", trade_date="2026-09-21").meta_path)


def test_another_writer_cannot_delete_in_use_cache(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    with level2_cache_lock(pm):
        with pytest.raises(RuntimeError, match="in use"):
            with level2_cache_lock(pm):
                pytest.fail("concurrent writer acquired the lock")
    with level2_cache_lock(pm):
        pass
