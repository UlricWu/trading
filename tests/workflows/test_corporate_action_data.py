# filepath: tests/workflows/test_corporate_action_data.py
"""Exercise evidence publication, source binding and replay-visible dispositions."""

from pathlib import Path
from datetime import date
from hashlib import sha256
import json
from unittest.mock import Mock

import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.announcement_publications import AnnouncementPublication
from src.data_system.context import DataContext
from src.jobs.requests import TrainingDateRange
from src.utils.path import PathManager
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.workflows.corporate_action_data import run_corporate_action_maintenance, run_corporate_action_preparation
from tests.fixtures.announcement_evidence import evidence


def test_workflow_reuses_exact_evidence_and_publishes_review_with_lineage(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, supplied, pdf = evidence
    monkeypatch.setattr(Access, "trade_dates", lambda self, **kwargs: list(context.trade_dates))
    registration = TrainingDateRange(start=context.start, end=context.end)
    announcement = TrainingDateRange(start="2024-01-01", end="2026-10-17")
    run_corporate_action_preparation(path_manager=pm, registration_interval=registration,
        announcement_interval=announcement, review_refs=(supplied,))
    resolution, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert resolution.status == "resolved"
    assert str(resolution.review.terms[0].reference_cash_per_share) == "0.28"
    paths = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start)
    before = paths.payload_path.read_bytes(), paths.payload_path.stat().st_mtime_ns
    run_corporate_action_preparation(path_manager=pm, registration_interval=registration,
        announcement_interval=announcement, review_refs=(supplied,))
    assert (paths.payload_path.read_bytes(), paths.payload_path.stat().st_mtime_ns) == before
    pdf.write_bytes(b"%PDF-changed")
    with pytest.raises(ValueError, match="PDF digest changed"):
        run_corporate_action_preparation(path_manager=pm, registration_interval=registration,
            announcement_interval=announcement, review_refs=(supplied,))
    assert paths.payload_path.read_bytes() == before[0]



def test_unmatched_pdf_publishes_specific_gap_and_no_distribution(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, _, _ = evidence
    monkeypatch.setattr(Access, "trade_dates", lambda self, **kwargs: list(context.trade_dates))
    run_corporate_action_preparation(path_manager=pm,
        registration_interval=TrainingDateRange(start=context.start, end=context.end),
        announcement_interval=TrainingDateRange(start="2024-01-01", end="2026-10-17"))
    resolution, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert resolution.status == "unresolved"
    assert resolution.reason == "missing_implementation"


@pytest.mark.parametrize("independent", [False, True])
def test_publication_change_refreshes_old_registration_requires_review_and_then_reuses_results(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
    independent: bool,
) -> None:
    pm, context, supplied, pdf = evidence
    monkeypatch.setattr(Access, "trade_dates", lambda self, **kwargs: list(context.trade_dates))
    if independent:
        source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
        source_table = pq.ParquetFile(source.payload_path).read()
        write_parquet_atomic(output_file=source.payload_path, table=source_table.slice(0, 0))
        meta.commit(pm=pm, payload_path=source.payload_path)
        collection_path = pdf.parent / "announcements.json"
        collection = AnnouncementCollection.model_validate_json(collection_path.read_bytes())
        original, = collection.documents
        collection = collection.model_copy(update={"dividend_sha256": sha256(source.payload_path.read_bytes()).hexdigest()})
        FileSystem.write_bytes_atomic(collection_path, collection.model_dump_json().encode())
        meta.commit(pm=pm, payload_path=collection_path)
        review, = json.loads(supplied.read_bytes())
        review.update(candidate_id="cninfo:1", action_id="cninfo:1:ordinary_shareholder", source_digest=original.sha256,
            announcement_source=AnnouncementPublication(source="cninfo",
                **original.model_dump(exclude={"sha256", "fetched_at"})).model_dump(mode="json"))
        supplied.write_text(json.dumps([review]))
    run_corporate_action_preparation(path_manager=pm,
        registration_interval=TrainingDateRange(start=context.start, end=context.end),
        announcement_interval=TrainingDateRange(start="2024-01-01", end="2026-10-17"), review_refs=(supplied,))
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    original, = collection.documents
    correction = original.model_copy(update={"announcement_id": "2", "title": "关于相关公告更正的说明",
        "published_date": date(2026, 9, 19), "url": "https://static.cninfo.com.cn/finalpage/2026-09-19/2.PDF"})
    (pdf.parent / "2.pdf").write_bytes(pdf.read_bytes())
    publication = AnnouncementPublication(source="cninfo", **correction.model_dump(exclude={"sha256", "fetched_at"}))
    unmatched = publication.model_copy(update={"symbol": "000001", "announcement_id": "3"})
    cninfo, bse = Mock(), Mock()
    cninfo.announcement_index.return_value = (publication, unmatched)
    bse.announcement_index.return_value = ()
    cninfo.stock_ids.return_value = {original.symbol: "fixture"}
    cninfo.company_index.return_value = (object(), object())
    cninfo.download_distribution_documents.return_value = (original, correction)
    monkeypatch.setattr("src.data_system.steps.collect_publications.CninfoBroker", Mock(return_value=cninfo))
    monkeypatch.setattr("src.data_system.steps.collect_publications.BseBroker", Mock(return_value=bse))
    monkeypatch.setattr("src.data_system.steps.collect_announcements.CninfoBroker", Mock(return_value=cninfo))
    publication_interval = TrainingDateRange(start="2026-09-19", end="2026-09-19")
    with pytest.raises(ValueError, match="renewed verification"):
        run_corporate_action_maintenance(path_manager=pm, publication_interval=publication_interval)
    assert cninfo.company_index.call_count == 1
    report = run_corporate_action_maintenance(path_manager=pm, publication_interval=publication_interval,
        review_refs=(supplied,))
    assert report.affected_registration_dates == (context.start,)
    assert report.resolution_counts == {"resolved": 1}
    assert report.unmatched_publications == (unmatched,)
    output = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start).payload_path
    before = output.read_bytes(), output.stat().st_mtime_ns
    repeated = run_corporate_action_maintenance(path_manager=pm, publication_interval=publication_interval)
    assert repeated.output_sha256 == report.output_sha256
    assert (output.read_bytes(), output.stat().st_mtime_ns) == before
    assert cninfo.company_index.call_count == 1


def test_independent_original_is_published_without_fabricating_dividend_rows_then_linked(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, supplied, _ = evidence
    monkeypatch.setattr(Access, "trade_dates", lambda self, **kwargs: list(context.trade_dates))
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    source_table = pq.ParquetFile(source.payload_path).read()
    collection_path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(collection_path.read_bytes())
    original, = collection.documents
    review, = json.loads(supplied.read_bytes())
    previous_id, previous_digest = review["candidate_id"], review["source_digest"]
    review.update(candidate_id="cninfo:1", action_id="cninfo:1:ordinary_shareholder", source_digest=original.sha256,
        announcement_source=AnnouncementPublication(source="cninfo",
            **original.model_dump(exclude={"sha256", "fetched_at"})).model_dump(mode="json"))
    supplied.write_text(json.dumps([review]))
    registration = TrainingDateRange(start=context.start, end=context.end)
    announcement = TrainingDateRange(start="2024-01-01", end="2026-10-17")
    for linked in (False, True):
        if linked:
            review["related_sources"] = {previous_id: previous_digest}
            supplied.write_text(json.dumps([review]))
        write_parquet_atomic(output_file=source.payload_path, table=source_table if linked else source_table.slice(0, 0))
        meta.commit(pm=pm, payload_path=source.payload_path)
        collection = collection.model_copy(update={"dividend_sha256": sha256(source.payload_path.read_bytes()).hexdigest()})
        FileSystem.write_bytes_atomic(collection_path, collection.model_dump_json().encode())
        meta.commit(pm=pm, payload_path=collection_path)
        run_corporate_action_preparation(path_manager=pm, registration_interval=registration,
            announcement_interval=announcement, review_refs=(supplied,))
        results = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
        assert len(results) == (2 if linked else 1)
        independent, = [row for row in results if row.candidate_id == "cninfo:1"]
        assert independent.source_row_ids == ()
        assert independent.source_digest == original.sha256
        assert {row.review.action_id for row in results} == {"cninfo:1:ordinary_shareholder"}
        assert pq.ParquetFile(source.payload_path).read().num_rows == (source_table.num_rows if linked else 0)
        output = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start).payload_path
        before = output.read_bytes(), output.stat().st_mtime_ns
        run_corporate_action_preparation(path_manager=pm, registration_interval=registration,
            announcement_interval=announcement)
        assert (output.read_bytes(), output.stat().st_mtime_ns) == before
