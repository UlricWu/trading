# filepath: tests/scripts/test_migrate_level2_archive.py
"""Real file migration preserves processed lineage and unmatched history."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import migrate_level2_archive as migration
from src.access import meta
from src.config.app_config import AppConfig
from src.config.data_config import BrokerConfig, DataConfig, SourceConfig
from src.data_system.brokers.level2 import Level2Broker
from src.utils.path import PathManager


def test_preflight_and_cleanup_preserve_lineage_and_unmatched_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = PathManager(tmp_path)
    sources = {name: SourceConfig(enabled=True, broker="level2_ftp", group="offline_level2", raw_object=raw, outputs=outputs) for name, raw, outputs in (
        ("sz_trade", "SZ_Trade", ["sz_trade"]), ("sz_order", "SZ_Order", []),
    )}
    config = AppConfig.model_construct(data=DataConfig(brokers={"level2_ftp": BrokerConfig(baidupcs_go="unused", remote_path_templates=("/level2/{date}/{file}",), raw_cache_days=1)}, sources=sources))
    monkeypatch.setattr(AppConfig, "load", Mock(return_value=config))
    broker = Mock(spec=Level2Broker)
    broker.name = "level2_ftp"
    monkeypatch.setattr(migration, "Level2Broker", Mock(return_value=broker, name="level2_ftp"))
    migration.Level2Broker.name = "level2_ftp"
    archives = {}
    originals = {}
    for date in ("2026-08-31", "2026-09-18", "2026-09-21", "2026-09-22"):
        for name, source in sources.items():
            raw = pm.raw_payload(broker=source.broker, source_name=name, trade_date=date, payload_file=f"{source.raw_object}.csv.7z")
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"raw")
            meta.commit(pm=pm, payload_path=raw)
            if name == "sz_trade":
                output = pm.processed_object(dataset_name=name, version="v1", trade_date=date)
                output.payload_path.parent.mkdir(parents=True)
                output.payload_path.write_bytes(b"processed")
                meta.commit(pm=pm, payload_path=output.payload_path, upstream_meta_path=raw.parent / "meta.json", symbol_slices={"000001": range(1)})
                originals[output.meta_path] = output.meta_path.read_bytes()
            if date != "2026-08-31":
                archive = meta.RemoteRawRecord(raw.name, 3, f"/level2/{date}/{raw.name}")
                archives[archive.remote_path] = archive
    broker.locate.side_effect = lambda *, raw_object, trade_date, expected_size: archives.get(f"/level2/{trade_date}/{raw_object}.csv.7z")
    broker.describe.side_effect = lambda path: archives.get(path)
    args = ["--storage-root", str(tmp_path), "--end", "2026-09-21"]
    assert migration.main(args) == 0
    assert isinstance(meta.find_level2_raw(pm=pm, meta_path=pm.raw_meta(broker="level2_ftp", source_name="sz_trade", trade_date="2026-09-21")), meta.MetaRecord)
    assert migration.main(args + ["--apply", "--cleanup"]) == 0
    for name, source in sources.items():
        raw_old = pm.raw_payload(broker=source.broker, source_name=name, trade_date="2026-09-18", payload_file=f"{source.raw_object}.csv.7z")
        assert not raw_old.exists()
        assert isinstance(meta.find_level2_raw(pm=pm, meta_path=raw_old.parent / "meta.json"), meta.RemoteRawRecord)
        missing = pm.raw_payload(broker=source.broker, source_name=name, trade_date="2026-08-31", payload_file=raw_old.name)
        assert missing.read_bytes() == b"raw"
        outside = pm.raw_payload(broker=source.broker, source_name=name, trade_date="2026-09-22", payload_file=raw_old.name)
        assert outside.read_bytes() == b"raw"
    for path, original in originals.items():
        assert path.read_bytes() == original
        meta.require(pm=pm, meta_path=path)
    assert migration.main(args + ["--apply", "--cleanup"]) == 0
    assert list((pm.level2_cache_dir() / ".migration-meta").glob("*/trade_date=*/meta.json"))
    assert migration.main(["--storage-root", str(tmp_path), "--apply", "--cleanup"]) == 0
    recent_cache = pm.staging_payload(
        broker="level2_ftp", source_name="sz_trade", trade_date="2026-09-22",
        payload_file="SZ_Trade.csv.7z",
    )
    assert recent_cache.read_bytes() == b"raw"
    assert not pm.raw_payload(
        broker="level2_ftp", source_name="sz_trade", trade_date="2026-09-22",
        payload_file="SZ_Trade.csv.7z",
    ).exists()
    for path, original in originals.items():
        assert path.read_bytes() == original
        meta.require(pm=pm, meta_path=path)


def test_cleanup_requires_explicit_apply(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        migration.main(["--storage-root", str(tmp_path), "--cleanup"])
    assert error.value.code == 2
