# filepath: tests/data_system/steps/test_announcements.py
"""Exercise evidence publication, source binding and replay-visible dispositions."""

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pypdf import PdfWriter
import pytest

from src.access import Access, meta
from src.data_system.brokers.cninfo import AnnouncementCollection, AnnouncementFile
from src.data_system.context import DataContext
from src.data_system.steps.collect_announcements import CollectAnnouncementsStep
from src.data_system.steps.extract_distribution_terms import ExtractDistributionTermsStep
from src.data_system.steps.resolve_corporate_actions import ResolveCorporateActionsStep
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


@pytest.fixture
def evidence(tmp_path: Path) -> tuple[PathManager, DataContext, Path, Path]:
    case = json.loads((Path(__file__).parents[2] / "fixtures/corporate_actions/verified_distributions.json").read_text())[1]
    day = case["review"]["record_date"]
    pm = PathManager(tmp_path)
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements", trade_date=day,
                          payload_file="announcements.json")
    path.parent.mkdir(parents=True)
    pdf = path.parent / "1.pdf"
    with PdfWriter() as writer:
        writer.add_blank_page(width=100, height=100)
        writer.write(pdf)
    document = AnnouncementFile(announcement_id="1", symbol=case["records"][0]["symbol"],
        title="fixture", published_date=case["review"]["terms"][0]["published_date"],
        url="https://static.cninfo.com.cn/finalpage/2024-07-18/1.PDF",
        sha256=sha256(pdf.read_bytes()).hexdigest(), fetched_at=datetime(2026, 10, 17, tzinfo=timezone.utc))
    collection = AnnouncementCollection(version="v1", dividend_sha256=sha256(source.payload_path.read_bytes()).hexdigest(),
        announcement_start="2024-01-01", announcement_end="2026-10-17",
        collected_at=datetime(2026, 10, 17, tzinfo=timezone.utc), documents=(document,),
        index_counts={document.symbol: 1}, request_sha256={})
    FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    case["review"]["evidence_refs"] = [document.url + "#sha256=" + document.sha256]
    supplied = tmp_path / "review-input.json"
    supplied.write_text(json.dumps([case["review"]]))
    return pm, DataContext(start=day, end=day, trade_dates=(day,)), supplied, pdf


def test_workflow_reuses_exact_evidence_and_publishes_review_with_lineage(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, pdf = evidence
    collection = CollectAnnouncementsStep(pm, date(2024, 1, 1), date(2026, 10, 17),
                                         lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    collection.run(context)
    extraction = ExtractDistributionTermsStep(pm, (supplied,))
    extraction.run(context)
    ResolveCorporateActionsStep(pm).run(context)
    resolution, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert resolution.status == "resolved"
    assert str(resolution.review.terms[0].reference_cash_per_share) == "0.28"
    paths = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start)
    before = paths.payload_path.read_bytes(), paths.payload_path.stat().st_mtime_ns
    extraction.run(context)
    ResolveCorporateActionsStep(pm).run(context)
    assert (paths.payload_path.read_bytes(), paths.payload_path.stat().st_mtime_ns) == before
    pdf.write_bytes(b"%PDF-changed")
    with pytest.raises(ValueError, match="PDF digest changed"):
        collection.run(context)
    with pytest.raises(ValueError, match="PDF digest changed"):
        extraction.run(context)
    assert paths.payload_path.read_bytes() == before[0]


def test_unmatched_pdf_publishes_specific_gap_and_no_distribution(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, _, _ = evidence
    ExtractDistributionTermsStep(pm).run(context)
    ResolveCorporateActionsStep(pm).run(context)
    resolution, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert resolution.status == "unresolved"
    assert resolution.reason == "missing_implementation"


def test_extraction_never_overwrites_existing_explicit_reviews_without_import(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
                            trade_date=context.start, payload_file="reviews.json")
    FileSystem.write_bytes_atomic(output, supplied.read_bytes())
    meta.commit(pm=pm, payload_path=output)
    with pytest.raises(ValueError, match="explicit import"):
        ExtractDistributionTermsStep(pm).run(context)
    assert output.read_bytes() == supplied.read_bytes()


def test_manual_review_with_wrong_original_hash_fails_before_publication(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    review, = json.loads(supplied.read_bytes())
    review["evidence_refs"] = ["https://static.cninfo.com.cn/other.PDF#sha256=" + "0" * 64]
    supplied.write_text(json.dumps([review]))
    with pytest.raises(ValueError, match="evidence does not match"):
        ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    assert Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start) is None


def test_new_announcement_collection_does_not_reuse_an_old_manual_review(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements", trade_date=context.start,
                          payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    collection = collection.model_copy(update={"announcement_end": date(2026, 10, 18)})
    FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    with pytest.raises(ValueError, match="renewed verification"):
        ExtractDistributionTermsStep(pm).run(context)


def test_partial_company_index_cannot_claim_full_dividend_partition_coverage(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, _, _ = evidence
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    with pq.ParquetFile(source.payload_path) as parquet:
        rows = parquet.read().to_pylist()
    rows.append({**rows[0], "symbol": "000001", "ts_code": "000001.SZ", "source_row_id": "1" * 64})
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements", trade_date=context.start,
                          payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes()).model_copy(update={
        "dividend_sha256": sha256(source.payload_path.read_bytes()).hexdigest(),
    })
    FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    with pytest.raises(ValueError, match="complete source-candidate coverage"):
        ExtractDistributionTermsStep(pm).run(context)
