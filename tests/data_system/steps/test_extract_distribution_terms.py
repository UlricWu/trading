# filepath: tests/data_system/steps/test_extract_distribution_terms.py
"""Reuse confirmed corrections and re-extract optional announcements without content fingerprints."""

from datetime import date, time
import json
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pypdf import PdfReader, PdfWriter

from src.access import Access, meta
from src.data_system.steps import extract_distribution_terms as extraction_module
from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.context import DataContext
from src.data_system.corporate_actions import CorporateActionReview
from src.data_system.steps.extract_distribution_terms import ExtractDistributionTermsStep
from src.data_system.steps.resolve_corporate_actions import ResolveCorporateActionsStep
from src.data_system.distribution_announcements import ReviewExtractionManifest
from src.utils.filesystem import FileSystem
from src.utils.datetime_utils import DateTimeUtils
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager
from tests.fixtures.announcement_evidence import evidence


@pytest.mark.parametrize("symbol,cash", [("000756", "0.03"), ("600167", "0.12")])
def test_shared_original_automatically_associates_duplicate_candidates(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch, symbol: str, cash: str,
) -> None:
    pm, _, _, _ = evidence
    cases = json.loads((Path(__file__).parents[2] /
        "fixtures/corporate_actions/implementation_publications.json").read_text())
    case = next(case for case in cases if case["symbol"] == symbol)
    day = case["records"][0]["record_date"]
    context = DataContext(start=day, end=day, trade_dates=(day,))
    paths = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=paths.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=paths.payload_path)
    document, = case["documents"]
    collection_path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=day, payload_file="announcements.json")
    collection = AnnouncementCollection(version="v1", collected_at="2026-10-25T00:00:00Z",
        announcement_start=document["published_date"], announcement_end=day,
        index_counts={symbol: 1}, request_files=(),
        documents=[{**{key: value for key, value in document.items() if key not in {"pages", "source"}},
            "fetched_at": "2026-10-25T00:00:00Z"}])
    FileSystem.write_bytes_atomic(collection_path, collection.model_dump_json().encode())
    FileSystem.write_bytes_atomic(collection_path.parent / f"{document['announcement_id']}.pdf", b"cached-original")
    meta.commit(pm=pm, payload_path=collection_path)
    pages = tuple(SimpleNamespace(extract_text=lambda page=page: page) for page in document["pages"])
    monkeypatch.setattr(extraction_module, "PdfReader", lambda stream: SimpleNamespace(pages=pages))
    ExtractDistributionTermsStep(pm).run(context)
    ResolveCorporateActionsStep(pm).run(context)
    results = Access(pm=pm, processed_version="v1").corporate_actions(record_date=day)
    assert results and len(results) == 2
    assert len({item.action_id for item in results}) == 1
    assert all(item.status == "resolved" for item in results)
    assert all(str(item.review.terms[0].cash_per_share) == cash for item in results)
    review = results[0].review
    assert {review.candidate_id, *review.related_sources} == {item.candidate_id for item in results}
    output = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=day).payload_path
    prior = output.read_bytes(), output.stat().st_mtime_ns
    ExtractDistributionTermsStep(pm).run(context)
    ResolveCorporateActionsStep(pm).run(context)
    assert (output.read_bytes(), output.stat().st_mtime_ns) == prior


def test_empty_schedule_preserves_the_context_without_publishing(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    context = DataContext(start="2024-07-23", end="2024-07-23", trade_dates=())

    assert ExtractDistributionTermsStep(pm).run(context) is context
    assert not any(pm.storage_root.rglob("*.json"))


@pytest.mark.parametrize("unreadable_title,required", [("差异化权益分派专项法律意见", False),
    ("关于除权参考价格计算结果的专项意见", False),
    ("关于除权参考价格计算结果的专项意见更正公告", True),
    ("权益分派实施公告更正的法律意见", True), ("2021年年度权益分派实施公告", False),
    ("2022年第一季度权益分派实施公告", False)])
def test_unreadable_documents_are_scoped_to_missing_event_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unreadable_title: str, required: bool,
) -> None:
    case = json.loads((Path(__file__).parents[2] / "fixtures/corporate_actions/body_matched_events.json").read_text())[2]
    pm = PathManager(tmp_path)
    day = "2023-06-13"
    paths = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=paths.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=paths.payload_path)
    docs = [row.copy() for row in case["documents"] if row["announcement_id"] != "1216616332"]
    for row in docs:
        if row["announcement_id"] == "1217001951":
            row.update(title=unreadable_title, published_date="2023-06-08")
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=day, payload_file="announcements.json")
    collection = AnnouncementCollection(version="v1", collected_at="2026-10-27T00:00:00Z",
        announcement_start="2023-01-01", announcement_end=day, index_counts={"603855": 2}, request_files=(),
        documents=[{**{k: v for k, v in row.items() if k not in {"source", "pages"}},
            "fetched_at": "2026-10-27T00:00:00Z"} for row in docs])
    FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    for row in docs:
        FileSystem.write_bytes_atomic(path.parent / f"{row['announcement_id']}.pdf", b"fixture-original")

    def reader(stream: object) -> SimpleNamespace:
        doc = next(row for row in docs if row["announcement_id"] == Path(stream.name).stem)
        return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda page=page: page) for page in doc["pages"]])

    monkeypatch.setattr(extraction_module, "PdfReader", reader)
    context = DataContext(start=day, end=day, trade_dates=(day,))
    ExtractDistributionTermsStep(pm).run(context)
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=day, payload_file="reviews.json")
    manifest = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    reviews = json.loads(output.read_bytes())
    if required:
        assert not reviews
        assert manifest.issues == {"603855.SH/2022-12-31/2023-06-13": "pdf_text_unreadable:cninfo:1217001951:empty_text"}
    else:
        assert len(reviews) == 1 and not manifest.issues
        assert reviews[0]["terms"][0]["cash_per_share"] == "1"


@pytest.mark.parametrize("title,relevant", [
    ("2020年年度审计报告（已取消）", False),
    ("2020年年度审计报告(已取消)", False),
    ("关于取消权益分派方案的公告", True),
    ("权益分派实施公告（已取消）", True),
    ("关于权益分派实施公告的更正公告", True),
])
def test_cancelled_document_status_does_not_make_an_unrelated_pdf_required(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
    title: str, relevant: bool,
) -> None:
    pm, context, supplied, _ = evidence
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    original, = collection.documents
    FileSystem.write_bytes_atomic(path, collection.model_copy(update={
        "documents": (original.model_copy(update={"title": title}),)}).model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    from src.data_system.distribution_announcements import DistributionExtraction
    review = CorporateActionReview.model_validate(json.loads(supplied.read_bytes())[0])
    monkeypatch.setattr(extraction_module, "extract_distribution_review",
        lambda *args: DistributionExtraction(review, review.reason))

    ExtractDistributionTermsStep(pm).run(context)

    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    manifest = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    if relevant:
        assert manifest.issues[review.candidate_id] == "pdf_text_unreadable:cninfo:1:empty_text"
        assert json.loads(output.read_bytes()) == []
    else:
        assert manifest.issues == {}
        assert json.loads(output.read_bytes()) == [review.model_dump(mode="json")]


def test_collection_window_extension_does_not_require_renewing_a_confirmed_review(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    step = ExtractDistributionTermsStep(pm=pm, review_refs=(supplied,))
    step.run(context)
    output = pm.raw_payload(broker='announcements', source_name='corporate_action_reviews',
        trade_date=context.start, payload_file='reviews.json')
    old_reviews = output.read_bytes()
    collection_path = pm.raw_payload(broker='cninfo', source_name='corporate_action_announcements',
        trade_date=context.start, payload_file='announcements.json')
    collection = AnnouncementCollection.model_validate_json(collection_path.read_bytes())
    collection = collection.model_copy(update={'announcement_end': date(2026, 10, 18)})
    FileSystem.write_bytes_atomic(collection_path, collection.model_dump_json(indent=2).encode())
    meta.commit(pm=pm, payload_path=collection_path)
    before = output.stat().st_mtime_ns
    ExtractDistributionTermsStep(pm).run(context)
    assert output.read_bytes() == old_reviews
    assert output.stat().st_mtime_ns == before
    assert not (output.parent / "history").exists()
    manual = pm.raw_payload(broker="announcements", source_name="corporate_action_review_inputs",
        trade_date=context.start, payload_file="reviews.json")
    assert not (manual.parent / "collection.sha256").exists()
    assert meta.require(pm=pm, meta_path=output.parent / "meta.json").payload_path == output


def test_manual_confirmation_uses_announcement_identity_without_a_content_digest(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    review, = json.loads(output.read_bytes())
    supplied_review, = json.loads(supplied.read_bytes())
    assert CorporateActionReview.model_validate(review) == CorporateActionReview.model_validate(supplied_review)
    assert "source_digest" not in review
    manifest = json.loads((output.parent / "extraction.json").read_bytes())
    assert set(manifest) == {"inputs", "issues"}


def test_committed_confirmation_is_reused_without_requiring_its_collection_again(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, pdf = evidence
    ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    ResolveCorporateActionsStep(pm).run(context)
    access = Access(pm=pm, processed_version="v1")
    confirmed = access.corporate_actions(record_date=context.start)
    (pdf.parent / "announcements.json").unlink()
    (pdf.parent / "meta.json").unlink()

    ExtractDistributionTermsStep(pm).run(context)
    ResolveCorporateActionsStep(pm).run(context)

    assert access.corporate_actions(record_date=context.start) == confirmed
    with pytest.raises(ValueError, match="evidence does not match"):
        ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    assert access.corporate_actions(record_date=context.start) == confirmed


def test_confirmed_facts_do_not_require_reparsing_every_related_pdf(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, supplied, _ = evidence

    def unexpected_pdf_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("confirmed facts already cover this candidate")

    monkeypatch.setattr(extraction_module, "PdfReader", unexpected_pdf_read)
    ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    ResolveCorporateActionsStep(pm).run(context)
    results = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    expected = CorporateActionReview.model_validate(json.loads(supplied.read_bytes())[0])
    assert results and all(item.review == expected for item in results)


def test_confirmed_absolute_reference_survives_preparation_and_resolution(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    reviews = json.loads(supplied.read_bytes())
    for terms in reviews[0]["terms"]:
        terms.update(reference_cash_per_share=None, reference_shares_per_share=None, reference_price="1.80")
    supplied.write_text(json.dumps(reviews))
    ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    ResolveCorporateActionsStep(pm).run(context)
    expected = CorporateActionReview.model_validate(reviews[0])
    results = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert results and all(item.review == expected for item in results)


def test_missing_bse_evidence_remains_a_gap_while_available_shares_can_be_reviewed(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    with pq.ParquetFile(source.payload_path) as parquet:
        rows = parquet.read().to_pylist()
    rows.append({**rows[0], "symbol": "920685", "ts_code": "920685.BJ"})
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
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


def test_failed_reextraction_keeps_a_previous_correction_blocking_source_fallback(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    original = supplied.read_bytes()
    FileSystem.write_bytes_atomic(output, original)
    manifest = ReviewExtractionManifest(inputs=(), issues={})
    FileSystem.write_bytes_atomic(output.parent / "extraction.json", manifest.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=output)

    ExtractDistributionTermsStep(pm).run(context)

    updated = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    candidate = json.loads(original)[0]["candidate_id"]
    assert updated.issues[candidate] == "previous_review_requires_confirmation:pdf_text_unreadable:cninfo:1:empty_text"
    assert json.loads(output.read_bytes()) == []
    archived, = (output.parent / "history").glob("*/reviews.json")
    assert archived.read_bytes() == original
    ExtractDistributionTermsStep(pm).run(context)
    repeated = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    assert repeated.issues[candidate] == "previous_review_requires_confirmation:pdf_text_unreadable:cninfo:1:empty_text"
    ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    confirmed = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    assert candidate not in confirmed.issues
    assert json.loads(output.read_bytes())[0]["candidate_id"] == candidate



def test_manual_review_with_unrelated_announcement_fails_before_publication(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    review, = json.loads(supplied.read_bytes())
    review["evidence_refs"] = ["https://static.cninfo.com.cn/other.PDF"]
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



def test_unrelated_announcement_does_not_invalidate_a_confirmed_review(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, supplied, _ = evidence
    ExtractDistributionTermsStep(pm, (supplied,)).run(context)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements", trade_date=context.start,
                          payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    original, = collection.documents
    unrelated = original.model_copy(update={"announcement_id": "2", "title": "董事会会议决议", "url": "https://example.org/2.PDF"})
    collection = collection.model_copy(update={"documents": (original, unrelated), "index_counts": {original.symbol: 2}})
    FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    ExtractDistributionTermsStep(pm).run(context)
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    review, = json.loads(output.read_bytes())
    assert review["evidence_refs"] == [original.url]



def test_partial_company_index_cannot_claim_full_dividend_partition_coverage(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, _, _ = evidence
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    with pq.ParquetFile(source.payload_path) as parquet:
        rows = parquet.read().to_pylist()
    rows.append({**rows[0], "symbol": "000001", "ts_code": "000001.SZ"})
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements", trade_date=context.start,
                          payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
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
    rows.append({**rows[0], "symbol": "000001", "ts_code": "000001.SZ"})
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    original, = collection.documents
    changed = original.model_copy(update={"title": "权益分派实施公告"})
    collection = collection.model_copy(update={"documents": (changed,),
        "index_counts": {original.symbol: 1, "000001": 1}})
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
    document = original.model_copy(update={"title": "权益分派实施公告"})
    FileSystem.write_bytes_atomic(path, collection.model_copy(update={"documents": (document,)}).model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)

    ExtractDistributionTermsStep(pm).run(context)

    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    manifest = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    candidate = json.loads(supplied.read_bytes())[0]["candidate_id"]
    assert manifest.issues[candidate] == "pdf_text_unreadable:cninfo:1:empty_text"


def test_reexecution_uses_current_extraction_rules_without_a_producer_fingerprint(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, supplied, _ = evidence
    ExtractDistributionTermsStep(pm).run(context)
    output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    assert json.loads(output.read_bytes()) == []
    from src.data_system.distribution_announcements import DistributionExtraction
    review = CorporateActionReview.model_validate(json.loads(supplied.read_bytes())[0])
    monkeypatch.setattr(extraction_module, "extract_distribution_review",
        lambda *args: DistributionExtraction(review, review.reason))

    ExtractDistributionTermsStep(pm).run(context)

    assert json.loads(output.read_bytes()) == [review.model_dump(mode="json")]
    manifest = ReviewExtractionManifest.model_validate_json((output.parent / "extraction.json").read_bytes())
    assert manifest.issues == {}
