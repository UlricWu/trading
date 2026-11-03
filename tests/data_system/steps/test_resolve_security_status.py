# filepath: tests/data_system/steps/test_resolve_security_status.py
"""Reviewed security status publication, source identity and public-time boundaries."""

from copy import deepcopy
from datetime import date, time
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.data_system.context import DataContext
from src.data_system.steps.resolve_security_status import ResolveSecurityStatusStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


@pytest.fixture
def reviewed_status(tmp_path: Path) -> tuple[PathManager, DataContext, dict[str, object]]:
    pm = PathManager(tmp_path)
    day = "2026-09-15"
    for name, table in (
        ("stock_basic", pa.table({"symbol": ["601238"], "list_date": ["2012-03-29"]})),
        ("suspend_d", pa.table({"symbol": ["601238", "601238"], "trade_date": [day, day],
                               "suspend_type": ["S", "R"], "suspend_timing": [None, None]})),
    ):
        paths = pm.processed_object(dataset_name=name, version="v1", trade_date=day)
        write_parquet_atomic(output_file=paths.payload_path, table=table)
        meta.commit(pm=pm, payload_path=paths.payload_path)
    path = pm.raw_payload(broker="announcements", source_name="security_status_reviews",
        trade_date=day, payload_file="reviews.json")
    payload = b"%PDF-1.4 fixed A-share suspension evidence"
    FileSystem.write_bytes_atomic(path.parent / "2026091401307.pdf", payload)
    url = "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0914/2026091401307.pdf"
    inputs = {"version": "v1", "documents": [{
        "announcement_id": "2026091401307", "symbol": "601238", "title": "A-share suspension",
        "published_date": "2026-09-14", "url": url,
        "fetched_at": "2026-10-18T00:00:00Z",
    }], "reviews": [{"ts_code": "601238.SH", "trade_date": day, "listing_status": "listed",
        "trading_status": "suspended", "available_ts_us": DateTimeUtils.local_time_to_utc_epoch_us(time(), date.fromisoformat(day)),
        "evidence_refs": [url], "reason": "A shares remain suspended; H-share resumption is separate"}]}
    FileSystem.write_bytes_atomic(path, json.dumps(inputs).encode())
    meta.commit(pm=pm, payload_path=path)
    return pm, DataContext(start=day, end=day, trade_dates=(day,)), inputs


def test_original_conflict_is_preserved_and_equal_confirmed_status_is_reused(
    reviewed_status: tuple[PathManager, DataContext, dict[str, object]],
) -> None:
    pm, context, inputs = reviewed_status
    step = ResolveSecurityStatusStep(pm=pm)
    step.run(context)
    access = Access(pm=pm, processed_version="v1")
    status, = access.security_status(trade_date=context.start)
    assert status.symbol == "601238" and status.trading_status == "suspended"
    assert status.evidence_refs == tuple(inputs["reviews"][0]["evidence_refs"])
    assert list(access.suspensions(trade_date=context.start)["suspend_type"]) == ["S", "R"]
    paths = pm.processed_object(dataset_name="security_status", version="v1", trade_date=context.start)
    review_path = pm.raw_payload(broker="announcements", source_name="security_status_reviews",
        trade_date=context.start, payload_file="reviews.json")
    with pq.ParquetFile(paths.payload_path) as parquet:
        references = json.loads(parquet.schema_arrow.metadata[b"security_status_inputs"])
    assert references == {"reviews": str(review_path.relative_to(pm.storage_root)), **{
        name: str(pm.processed_object(dataset_name=name, version="v1",
            trade_date=context.start).payload_path.relative_to(pm.storage_root))
        for name in ("stock_basic", "suspend_d")}}
    before = (paths.payload_path.stat().st_mtime_ns, paths.meta_path.stat().st_mtime_ns)
    step.run(context)
    assert (paths.payload_path.stat().st_mtime_ns, paths.meta_path.stat().st_mtime_ns) == before


def test_source_content_is_not_a_gate_for_confirmed_facts(
    reviewed_status: tuple[PathManager, DataContext, dict[str, object]],
) -> None:
    pm, context, _ = reviewed_status
    step = ResolveSecurityStatusStep(pm=pm)
    step.run(context)
    output = pm.processed_object(dataset_name="security_status", version="v1", trade_date=context.start)
    before = output.payload_path.read_bytes()
    before_time = output.payload_path.stat().st_mtime_ns
    path = pm.processed_object(dataset_name="suspend_d", version="v1", trade_date=context.start).payload_path
    write_parquet_atomic(output_file=path, table=pa.table({"symbol": ["601238"],
        "trade_date": [context.start], "suspend_type": ["S"], "suspend_timing": [None]}))
    meta.commit(pm=pm, payload_path=path)
    access = Access(pm=pm, processed_version="v1")
    assert access.security_status(trade_date=context.start)[0].trading_status == "suspended"
    step.run(context)
    assert access.security_status(trade_date=context.start)[0].trading_status == "suspended"
    assert output.payload_path.read_bytes() == before
    assert output.payload_path.stat().st_mtime_ns == before_time


@pytest.mark.parametrize("changed_fact", [False, True])
def test_review_changes_compare_actual_facts_and_refresh_upstream_metadata(
    reviewed_status: tuple[PathManager, DataContext, dict[str, object]], changed_fact: bool,
) -> None:
    pm, context, inputs = reviewed_status
    step = ResolveSecurityStatusStep(pm=pm)
    step.run(context)
    output = pm.processed_object(dataset_name="security_status", version="v1", trade_date=context.start)
    before = output.payload_path.read_bytes()
    before_time = output.payload_path.stat().st_mtime_ns
    changed = deepcopy(inputs)
    if changed_fact:
        changed["reviews"][0]["trading_status"] = "trading"
        changed["reviews"][0]["reason"] = "Confirmed A-share resumption"
    review_path = pm.raw_payload(broker="announcements", source_name="security_status_reviews",
        trade_date=context.start, payload_file="reviews.json")
    FileSystem.write_bytes_atomic(review_path, json.dumps(changed, indent=2).encode())
    meta.commit(pm=pm, payload_path=review_path)

    step.run(context)

    status, = Access(pm=pm, processed_version="v1").security_status(trade_date=context.start)
    assert status.trading_status == ("trading" if changed_fact else "suspended")
    if changed_fact:
        assert output.payload_path.read_bytes() != before
    else:
        assert output.payload_path.read_bytes() == before
        assert output.payload_path.stat().st_mtime_ns == before_time
    loaded = meta.require(pm=pm, meta_path=output.meta_path, expected_payload_path=output.payload_path)
    assert loaded.upstream == (review_path.with_name("meta.json").relative_to(pm.storage_root), review_path.stat().st_size)


def test_consumer_rejects_a_different_source_day(
    reviewed_status: tuple[PathManager, DataContext, dict[str, object]],
) -> None:
    pm, context, _ = reviewed_status
    ResolveSecurityStatusStep(pm=pm).run(context)
    output = pm.processed_object(dataset_name="security_status", version="v1", trade_date=context.start)
    with pq.ParquetFile(output.payload_path) as parquet:
        table = parquet.read()
    metadata = dict(table.schema.metadata)
    references = json.loads(metadata[b"security_status_inputs"])
    references["stock_basic"] = str(pm.processed_object(dataset_name="stock_basic", version="v1",
        trade_date="2026-09-16").payload_path.relative_to(pm.storage_root))
    metadata[b"security_status_inputs"] = json.dumps(references).encode()
    write_parquet_atomic(output_file=output.payload_path, table=table.replace_schema_metadata(metadata))
    meta.commit(pm=pm, payload_path=output.payload_path)
    with pytest.raises(ValueError, match="input identities differ"):
        Access(pm=pm, processed_version="v1").security_status(trade_date=context.start)


def test_later_announcement_cannot_confirm_an_earlier_day(
    reviewed_status: tuple[PathManager, DataContext, dict[str, object]],
) -> None:
    pm, context, inputs = reviewed_status
    changed = deepcopy(inputs)
    changed["documents"][0]["published_date"] = "2026-09-29"
    path = pm.raw_payload(broker="announcements", source_name="security_status_reviews",
        trade_date=context.start, payload_file="reviews.json")
    FileSystem.write_bytes_atomic(path, json.dumps(changed).encode())
    meta.commit(pm=pm, payload_path=path)
    with pytest.raises(ValueError, match="before its evidence"):
        ResolveSecurityStatusStep(pm=pm).run(context)
    assert not pm.processed_object(dataset_name="security_status", version="v1",
        trade_date=context.start).payload_path.exists()


def test_missing_pdf_does_not_publish_a_status(
    reviewed_status: tuple[PathManager, DataContext, dict[str, object]],
) -> None:
    pm, context, _ = reviewed_status
    path = pm.raw_payload(broker="announcements", source_name="security_status_reviews",
        trade_date=context.start, payload_file="reviews.json")
    (path.parent / "2026091401307.pdf").unlink()
    with pytest.raises(FileNotFoundError):
        ResolveSecurityStatusStep(pm=pm).run(context)
    assert not pm.processed_object(dataset_name="security_status", version="v1",
        trade_date=context.start).payload_path.exists()


def test_no_review_does_not_claim_confirmed_coverage(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    context = DataContext(start="2026-09-15", end="2026-09-15", trade_dates=("2026-09-15",))
    ResolveSecurityStatusStep(pm=pm).run(context)
    assert Access(pm=pm, processed_version="v1").security_status(trade_date=context.start) == ()
