# filepath: tests/data_system/steps/test_extract_distribution_terms.py
"""Rebuild reviewed derivatives after an explicitly rechecked collection changes."""

from datetime import date, time
from hashlib import sha256
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pypdf import PdfReader, PdfWriter

from src.access import Access, meta
from src.data_system.steps import extract_distribution_terms as extraction_module
from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.context import DataContext
from src.data_system.steps.extract_distribution_terms import ExtractDistributionTermsStep
from src.data_system.distribution_announcements import ReviewExtractionManifest
from src.utils.filesystem import FileSystem
from src.utils.datetime_utils import DateTimeUtils
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager
from tests.fixtures.announcement_evidence import evidence


def test_empty_schedule_preserves_the_context_without_publishing(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    context = DataContext(start="2024-07-23", end="2024-07-23", trade_dates=())

    assert ExtractDistributionTermsStep(pm).run(context) is context
    assert not any(pm.storage_root.rglob("*.json"))


def test_renewed_review_rebuilds_after_collection_size_changes(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    step = ExtractDistributionTermsStep(pm=pm, review_refs=(supplied,))
    step.run(context)
    output = pm.raw_payload(broker='announcements', source_name='corporate_action_reviews',
        trade_date=context.start, payload_file='reviews.json')
    old_reviews = output.read_bytes()
    old_manifest = (output.parent / 'extraction.json').read_bytes()
    collection_path = pm.raw_payload(broker='cninfo', source_name='corporate_action_announcements',
        trade_date=context.start, payload_file='announcements.json')
    collection = AnnouncementCollection.model_validate_json(collection_path.read_bytes())
    collection = collection.model_copy(update={'announcement_end': date(2026, 10, 18)})
    FileSystem.write_bytes_atomic(collection_path, collection.model_dump_json(indent=2).encode())
    meta.commit(pm=pm, payload_path=collection_path)
    step.run(context)
    archives = tuple((output.parent / 'history').glob('*/reviews.json'))
    assert len(archives) == 1
    assert archives[0].read_bytes() == old_reviews
    assert (archives[0].parent / 'extraction.json').read_bytes() == old_manifest
    manual = pm.raw_payload(broker='announcements', source_name='corporate_action_review_inputs',
        trade_date=context.start, payload_file='reviews.json')
    previous_binding, = (manual.parent / 'history').glob('*/collection.sha256')
    assert previous_binding.read_text() != (manual.parent / 'collection.sha256').read_text()
    assert meta.require(pm=pm, meta_path=output.parent / 'meta.json').payload_path == output
    before = output.stat().st_mtime_ns
    step.run(context)
    assert output.stat().st_mtime_ns == before


def test_changed_original_pdf_is_rejected_before_publishing_reviews(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, pdf = evidence
    pdf.write_bytes(b"%PDF-changed")
    with pytest.raises(ValueError, match="PDF digest changed"):
        ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    assert not output.exists()
    manual = pm.raw_payload(broker="announcements", source_name="corporate_action_review_inputs",
        trade_date=context.start, payload_file="reviews.json")
    assert not manual.exists()


def test_missing_bse_evidence_remains_a_gap_while_available_shares_can_be_reviewed(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    with pq.ParquetFile(source.payload_path) as parquet:
        rows = parquet.read().to_pylist()
    rows.append({**rows[0], "symbol": "920685", "ts_code": "920685.BJ", "source_row_id": "1" * 64})
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes()).model_copy(update={
        "dividend_sha256": sha256(source.payload_path.read_bytes()).hexdigest()})
    FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    reviews = json.loads(output.read_bytes())
    assert len(reviews) == 1
    assert reviews[0]["candidate_id"] == json.loads(supplied.read_bytes())[0]["candidate_id"]
    manifest = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    assert tuple(manifest.issues.values()) == ("announcements_not_collected:bse",)


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


@pytest.mark.parametrize("collected_time", [False, True])
def test_precise_review_time_requires_original_index_evidence(
    evidence: tuple[PathManager, DataContext, Path, Path], collected_time: bool,
) -> None:
    pm, context, supplied, pdf = evidence
    review, = json.loads(supplied.read_bytes())
    terms, = review["terms"]
    timestamp = DateTimeUtils.local_time_to_utc_epoch_us(time(7, 41, 10), date.fromisoformat(terms["published_date"]))
    terms["published_ts_us"] = timestamp
    supplied.write_text(json.dumps([review]))
    if collected_time:
        path = pdf.parent / "announcements.json"
        collection = AnnouncementCollection.model_validate_json(path.read_bytes())
        document, = collection.documents
        collection = collection.model_copy(update={"documents": (document.model_copy(update={"published_ts_us": timestamp}),)})
        FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
        meta.commit(pm=pm, payload_path=path)
        ExtractDistributionTermsStep(pm, (supplied,)).run(context)
        output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
            trade_date=context.start, payload_file="reviews.json")
        assert json.loads(output.read_bytes())[0]["terms"][0]["published_ts_us"] == timestamp
    else:
        with pytest.raises(ValueError, match="publication time lacks collected index evidence"):
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


@pytest.mark.parametrize("manually_reviewed", [False, True])
@pytest.mark.parametrize("read_failure", ["UnicodeDecodeError", "empty_text"])
def test_pdf_text_decoding_failure_requires_review_without_losing_other_candidates(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
    manually_reviewed: bool, read_failure: str,
) -> None:
    pm, context, supplied, _ = evidence
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    with pq.ParquetFile(source.payload_path) as parquet:
        rows = parquet.read().to_pylist()
    rows.append({**rows[0], "symbol": "000001", "ts_code": "000001.SZ", "source_row_id": "1" * 64})
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    original, = collection.documents
    changed = original.model_copy(update={"title": "权益分派实施公告"})
    collection = collection.model_copy(update={"documents": (changed,),
        "index_counts": {original.symbol: 1, "000001": 1},
        "dividend_sha256": sha256(source.payload_path.read_bytes()).hexdigest()})
    FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)

    def unreadable_text(self: object) -> str:
        if read_failure == "UnicodeDecodeError":
            raise UnicodeDecodeError("utf-16-be", b")", 0, 1, "truncated data")
        return ""

    page = PdfReader(path.parent / "1.pdf").pages[0]
    monkeypatch.setattr(type(page), "extract_text", unreadable_text)
    ExtractDistributionTermsStep(pm, (supplied,) if manually_reviewed else ()).run(context)
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    reviews = json.loads(output.read_bytes())
    assert len(reviews) == int(manually_reviewed)
    manifest = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    candidate = json.loads(supplied.read_bytes())[0]["candidate_id"]
    if manually_reviewed:
        assert candidate not in manifest.issues
    else:
        assert manifest.issues[candidate] == f"pdf_text_unreadable:cninfo:1:{read_failure}"
    other = f"000001.SZ/{rows[0]['end_date']}/{context.start}"
    assert manifest.issues[other] == "missing_implementation"


@pytest.mark.parametrize("algorithm", ["AES-128", "AES-256"])
def test_public_aes_pdf_is_read_without_an_opening_password(
    evidence: tuple[PathManager, DataContext, Path, Path], algorithm: str,
) -> None:
    pm, context, supplied, pdf = evidence
    with PdfWriter(clone_from=pdf) as writer:
        writer.encrypt(user_password="", owner_password="fixture-owner", algorithm=algorithm)
        writer.write(pdf)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    original, = collection.documents
    document = original.model_copy(update={"title": "权益分派实施公告",
        "sha256": sha256(pdf.read_bytes()).hexdigest()})
    FileSystem.write_bytes_atomic(path, collection.model_copy(update={"documents": (document,)}).model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)

    ExtractDistributionTermsStep(pm).run(context)

    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    manifest = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    candidate = json.loads(supplied.read_bytes())[0]["candidate_id"]
    assert manifest.issues[candidate] == "pdf_text_unreadable:cninfo:1:empty_text"


def test_pdf_crypto_dependency_change_invalidates_cached_extraction(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, supplied, _ = evidence
    ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    manifest_path = output.parent / "extraction.json"
    before = ReviewExtractionManifest.model_validate_json(manifest_path.read_bytes())
    original_version = extraction_module.version
    monkeypatch.setattr(extraction_module, "version", lambda name:
        "different-crypto-version" if name == "cryptography" else original_version(name))

    ExtractDistributionTermsStep(pm).run(context)

    after = ReviewExtractionManifest.model_validate_json(manifest_path.read_bytes())
    assert after.producer_sha256 != before.producer_sha256
    assert after.inputs == before.inputs
    assert after.reviews_sha256 == before.reviews_sha256
    assert any(ReviewExtractionManifest.model_validate_json(path.read_bytes()) == before
               for path in (output.parent / "history").glob("*/extraction.json"))
