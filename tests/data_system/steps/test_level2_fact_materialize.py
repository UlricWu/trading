# filepath: tests/data_system/steps/test_level2_fact_materialize.py
"""Processed-first Level-2 materialization and cloud-only order regressions."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import meta
from src.config.data_config import SourceConfig
from src.data_system.brokers.level2 import Level2Broker
from src.data_system.context import DataContext
from src.data_system.normalize import NormalizeOutput
from src.data_system.normalize.level2 import normalize_level2
from src.data_system.steps.level2_fact_materialize import Level2FactMaterializeStep
from src.utils.path import PathManager


def _sources() -> dict[str, SourceConfig]:
    return {
        name: SourceConfig(enabled=True, broker="level2_ftp", group="offline_level2",
                           raw_object=raw, outputs=outputs)
        for name, raw, outputs in (
            ("sh_stock_ordertrade", "SH_Stock_OrderTrade", ["sh_trade"]),
            ("sz_order", "SZ_Order", []),
            ("sz_trade", "SZ_Trade", ["sz_trade"]),
        )
    }


def _normalize() -> Mock:
    return Mock(return_value=NormalizeOutput(pa.table({"value": [1]}), {"000001": range(1)}))


def test_only_needed_raw_downloads_and_full_hit_works_after_cache_removed(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    broker = Mock(spec=Level2Broker)
    broker.locate.side_effect = lambda *, raw_object, trade_date: meta.RemoteRawRecord(
        f"{raw_object}.csv.7z", 3, f"/level2/{trade_date}/{raw_object}.csv.7z",
    )

    def download(*, record: meta.RemoteRawRecord, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"raw")
        return destination

    broker.download.side_effect = download
    get_broker = Mock(return_value=broker)
    normalize = _normalize()
    step = Level2FactMaterializeStep(
        path_manager=pm, sources=_sources(), get_broker=get_broker,
        normalize_operation=normalize, processed_version="v1", raw_cache_days=5,
    )
    context = DataContext(start="2026-09-21", end="2026-09-21", trade_dates=("2026-09-21",))
    assert step.run(context) is context
    assert [call.kwargs["raw_object"] for call in broker.locate.call_args_list] == ["SH_Stock_OrderTrade", "SZ_Trade"]
    assert normalize.call_count == 2
    assert not (pm.storage_root / "raw/level2_ftp/sz_order").exists()
    for cache in pm.level2_cache_dir().glob("*/trade_date=*/*.csv.7z"):
        cache.unlink()
    get_broker.reset_mock()
    get_broker.side_effect = AssertionError("processed hits must work offline")
    normalize.reset_mock()
    assert step.run(context) is context
    get_broker.assert_not_called()
    normalize.assert_not_called()
    # A missing output fetches only its own compressed source, using the pinned path.
    sz = pm.processed_object(dataset_name="sz_trade", version="v1", trade_date=context.start)
    sz.meta_path.unlink()
    get_broker.side_effect = None
    broker.locate.reset_mock()
    broker.download.reset_mock()
    assert step.run(context) is context
    broker.locate.assert_not_called()
    assert broker.download.call_count == 1
    assert broker.download.call_args.kwargs["record"].payload == "SZ_Trade.csv.7z"


def test_raw_only_source_never_contacts_cloud_or_normalizes(tmp_path: Path) -> None:
    get_broker = Mock(side_effect=AssertionError("no order download"))
    normalize = _normalize()
    step = Level2FactMaterializeStep(
        path_manager=PathManager(tmp_path), sources={"sz_order": _sources()["sz_order"]},
        get_broker=get_broker, normalize_operation=normalize,
        processed_version="v1", raw_cache_days=5,
    )
    step.run(DataContext(start="2026-09-21", end="2026-09-21", trade_dates=("2026-09-21",)))
    get_broker.assert_not_called()
    normalize.assert_not_called()


def test_missing_cloud_dates_reported_and_corrupt_normalization_not_published(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    broker = Mock(spec=Level2Broker)
    broker.locate.return_value = None
    normalize = _normalize()
    step = Level2FactMaterializeStep(
        path_manager=pm, sources={"sz_trade": _sources()["sz_trade"]},
        get_broker=lambda: broker, normalize_operation=normalize,
        processed_version="v1", raw_cache_days=5,
    )
    context = DataContext(start="2026-09-21", end="2026-09-22", trade_dates=("2026-09-21", "2026-09-22"))
    with pytest.raises(RuntimeError, match="sz_trade@2026-09-21.*sz_trade@2026-09-22"):
        step.run(context)
    assert broker.locate.call_count == 2
    normalize.assert_not_called()
    archive = meta.RemoteRawRecord("SZ_Trade.csv.7z", 3, "/level2/2026-09-21/SZ_Trade.csv.7z")
    raw_meta = pm.raw_meta(broker="level2_ftp", source_name="sz_trade", trade_date=context.start)
    meta.commit_remote_raw(pm=pm, meta_path=raw_meta, record=archive)
    cache = pm.staging_payload(broker="level2_ftp", source_name="sz_trade", trade_date=context.start, payload_file=archive.payload)
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"raw")
    normalize.side_effect = ValueError("bad CSV")
    with pytest.raises(ValueError, match="bad CSV"):
        step.run(context)
    assert cache.read_bytes() == b"raw"
    assert not pm.processed_object(dataset_name="sz_trade", version="v1", trade_date=context.start).meta_path.exists()
    broker.download.assert_not_called()


@pytest.mark.contract
def test_level2_step_normalizes_cached_baidu_archives_and_keeps_orders_cloud_only(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-09-21"
    payloads = {
        "sh_stock_ordertrade": (
            "SH_Stock_OrderTrade", ["sh_trade"],
            "TradeTime,ExchangeID,SecurityID,TickTime,TickType,Price,Volume,Side,MainSeq,SubSeq,BuyNo,SellNo\n"
            "2026-09-21 09:30:00.123,SH,600000,093000123,T,10.1000,100,B,9,0,5,4\n"
            "2026-09-21 09:30:00.123,SH,600000,093000123,D,10.1000,100,B,10,0,5,0\n",
        ),
        "sz_trade": (
            "SZ_Trade", ["sz_trade"],
            "TradeTime,ExchangeID,SecurityID,TickTime,ExecType,TradePrice,TradeVolume,MainSeq,SubSeq,BuyNo,SellNo\n"
            "2026-09-21 09:30:00.120,SZ,000001,093000120,F,9.8000,200,11,0,10,9\n"
            "2026-09-21 09:30:00.130,SZ,000001,093000130,4,9.8000,200,12,0,10,0\n",
        ),
        "sz_order": (
            "SZ_Order", [],
            "TradeTime,ExchangeID,SecurityID,OrderTime,Price,Volume,Side,OrderType,MainSeq,SubSeq,OrderNO,OrderStatus,LocalTimeStamp\n"
            "2026-09-21 09:30:00.100,SZ,000001,093000100,9.8000,200,1,U,10,0,10,,2026-09-21 09:30:00.110\n",
        ),
    }
    sources = {}
    for source_name, (raw_object, outputs, csv) in payloads.items():
        sources[source_name] = SourceConfig(
            enabled=True, broker="level2_ftp", group="offline_level2",
            raw_object=raw_object, outputs=outputs,
        )
        if not outputs:
            continue
        csv_path = tmp_path / f"{raw_object}.csv"
        csv_path.write_text(csv, encoding="utf-8")
        raw_path = pm.staging_payload(
            broker="level2_ftp", source_name=source_name,
            trade_date=trade_date, payload_file=f"{raw_object}.csv.7z",
        )
        raw_path.parent.mkdir(parents=True)
        subprocess.run(
            ["7zz", "a", "-t7z", "-mx=1", str(raw_path), str(csv_path)],
            check=True, capture_output=True,
        )
        raw_meta = pm.raw_meta(
            broker="level2_ftp", source_name=source_name, trade_date=trade_date,
        )
        meta.commit_remote_raw(
            pm=pm, meta_path=raw_meta,
            record=meta.RemoteRawRecord(
                raw_path.name, raw_path.stat().st_size,
                f"/level2/{trade_date}/{raw_path.name}",
            ),
        )

    get_broker = Mock(side_effect=AssertionError("warm cache must not access cloud"))
    step = Level2FactMaterializeStep(
        path_manager=pm, sources=sources, get_broker=get_broker,
        normalize_operation=normalize_level2, processed_version="v1", raw_cache_days=5,
    )
    context = DataContext(start=trade_date, end=trade_date, trade_dates=(trade_date,))
    assert step.run(context) is context
    saved_files = {}
    for source_name, dataset_name, price in (
        ("sh_stock_ordertrade", "sh_trade", 10.1),
        ("sz_trade", "sz_trade", 9.8),
    ):
        paths = pm.processed_object(
            dataset_name=dataset_name, version="v1", trade_date=trade_date,
        )
        record = meta.require(pm=pm, meta_path=paths.meta_path)
        assert record.upstream is not None
        assert str(record.upstream[0]) == str(
            pm.raw_meta(broker="level2_ftp", source_name=source_name, trade_date=trade_date)
            .relative_to(pm.storage_root)
        )
        assert record.symbol_slices == {
            "600000" if dataset_name == "sh_trade" else "000001": range(0, 1)
        }
        table = pq.ParquetFile(record.payload_path).read()
        assert table.num_rows == 1
        assert table["price"].to_pylist() == [price]
        assert table["sub_seq"].to_pylist() == [0]
        saved_files[paths.payload_path] = paths.payload_path.stat().st_mtime_ns
        saved_files[paths.meta_path] = paths.meta_path.stat().st_mtime_ns
    assert not (pm.storage_root / "raw" / "level2_ftp" / "sz_order").exists()
    assert not (pm.level2_cache_dir() / "sz_order").exists()
    assert not (pm.storage_root / "processed" / "sz_order").exists()
    assert step.run(context) is context
    get_broker.assert_not_called()
    assert {path: path.stat().st_mtime_ns for path in saved_files} == saved_files

