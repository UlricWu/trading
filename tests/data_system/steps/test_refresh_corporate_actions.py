# filepath: tests/data_system/steps/test_refresh_corporate_actions.py
"""Exercise evidence publication, source binding and replay-visible dispositions."""

from pathlib import Path
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests

from src.access import Access, meta
from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.announcement_publications import AnnouncementPublication
from src.data_system.context import DataContext
from src.jobs.requests import TrainingDateRange
from src.utils.path import PathManager
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.data_system.steps.refresh_corporate_actions import RefreshCorporateActionsStep
from src.data_system.steps.collect_announcements import CollectAnnouncementsStep
from src.data_system.steps.collect_bse_announcements import CollectBseAnnouncementsStep
from src.data_system.steps.collect_publications import CollectPublicationsStep
from src.data_system.announcement_maintenance import prepare_announcement_updates
from tests.fixtures.announcement_evidence import evidence


def _prepare_evidence(*, path_manager, registration_interval, announcement_interval, review_refs=()):
    context = DataContext(start=announcement_interval.start, end=announcement_interval.end,
        trade_dates=tuple(Access(pm=path_manager, processed_version="v1").trade_dates(
            start_date=registration_interval.start, end_date=registration_interval.end)))
    for factory in (CollectAnnouncementsStep, CollectBseAnnouncementsStep):
        factory(pm=path_manager, announcement_start=date.fromisoformat(context.start),
            announcement_end=date.fromisoformat(context.end), clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc),
            review_refs=review_refs).run(context)
    RefreshCorporateActionsStep(pm=path_manager, clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc),
        review_refs=review_refs).run(context)


def _refresh_publications(*, path_manager, publication_interval):
    context = DataContext(start=publication_interval.start, end=publication_interval.end)
    clock = lambda: datetime(2026, 10, 28, tzinfo=timezone.utc)
    CollectPublicationsStep(pm=path_manager, clock=clock).run(context)
    RefreshCorporateActionsStep(pm=path_manager, clock=clock).run(context)
    updates = prepare_announcement_updates(path_manager=path_manager,
        start=date.fromisoformat(context.start), end=date.fromisoformat(context.end))
    dates = tuple(sorted({item.registration_date for item in updates.refreshes}))
    results = [row for day in dates for row in Access(pm=path_manager, processed_version="v1").corporate_actions(record_date=day)]
    return SimpleNamespace(affected_registration_dates=dates,
        resolution_counts={status: sum(row.status == status for row in results) for status in {row.status for row in results}},
        unmatched_publications=updates.unmatched_publications,
        output_refs=tuple(str(path_manager.processed_object(dataset_name="corporate_actions", version="v1",
            trade_date=day).payload_path) for day in dates))


@pytest.mark.parametrize("symbol", ["300016", "300116", "600515"])
def test_preparation_preserves_source_payment_and_confirmed_reorganization_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, symbol: str,
) -> None:
    fixture = "implementation_publications" if symbol == "300016" else "confirmed_compensations"
    cases = json.loads((Path(__file__).parents[2] / f"fixtures/corporate_actions/{fixture}.json").read_text())
    case = next(case for case in cases if case["records"][0]["symbol"] == symbol)
    documents = case["documents"]
    day = case["records"][0]["record_date"]
    pm = PathManager(tmp_path)
    paths = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=paths.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=paths.payload_path)
    source_bytes = paths.payload_path.read_bytes()
    start = min(doc["published_date"] for doc in documents)
    end = max(doc["published_date"] for doc in documents)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=day, payload_file="announcements.json")
    collection = AnnouncementCollection(version="v1", collected_at="2026-10-28T00:00:00Z",
        announcement_start=start, announcement_end=end, index_counts={symbol: len(documents)}, request_files=(),
        documents=[{**{k: v for k, v in doc.items() if k not in {"pages", "source"}},
            "fetched_at": "2026-10-28T00:00:00Z"} for doc in documents])
    FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    for doc in documents:
        FileSystem.write_bytes_atomic(path.parent / f"{doc['announcement_id']}.pdf", b"fixture-original")
    supplied = tmp_path / "confirmed-facts.json"
    supplied.write_text(json.dumps([case["review"]] if "review" in case else []))

    def reader(stream: object) -> SimpleNamespace:
        doc = next(doc for doc in documents if doc["announcement_id"] == Path(stream.name).stem)
        return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda page=page: page) for page in doc["pages"]])

    monkeypatch.setattr("src.data_system.steps.extract_distribution_terms.PdfReader", reader)
    monkeypatch.setattr(Access, "trade_dates", lambda self, **kwargs: [day])
    observed = []
    for refs in [(supplied,) if "review" in case else (), ()]:
        _prepare_evidence(path_manager=pm,
            registration_interval=TrainingDateRange(start=day, end=day),
            announcement_interval=TrainingDateRange(start=start, end=end), review_refs=refs)
        result, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=day)
        assert result.status == "resolved"
        if symbol == "300016":
            assert result.review is None and result.reason == "ordinary_dividend_source"
            assert result.source_record.pay_date == date(2019, 4, 3)
            output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
                trade_date=day, payload_file="reviews.json")
            manifest = json.loads((output.parent / "extraction.json").read_bytes())
            assert manifest["issues"] == {result.candidate_id:
                "announcement_payment_year_conflict:1205941522:pdf=2018-04-03:dividend=2019-04-03"}
        else:
            assert result.source_record is None
            assert result.review.model_dump(mode="json") == case["review"]
        output = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=day).payload_path
        observed.append((output.read_bytes(), output.stat().st_mtime_ns))
    assert observed[0] == observed[1]
    assert paths.payload_path.read_bytes() == source_bytes


@pytest.mark.parametrize("generic_title", [False, True])
@pytest.mark.parametrize("cached_miss", [False, True])
@pytest.mark.parametrize("case_index,announcement_ids", [(0, ("1218675470", "1218734554")), (1, ("1224645547", "1224652944"))])
def test_distribution_index_to_resolution_recovers_body_matched_zero_entitlements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generic_title: bool, cached_miss: bool,
    case_index: int, announcement_ids: tuple[str, str],
) -> None:
    case = json.loads((Path(__file__).parents[2] /
        "fixtures/corporate_actions/body_matched_events.json").read_text())[case_index]
    documents = [row.copy() for row in case["documents"] if row["announcement_id"] in announcement_ids]
    if generic_title:
        documents[0]["title"] = "提示性公告"
    pm = PathManager(tmp_path)
    source = case["records"][0]
    day = source["record_date"]
    symbol = source["symbol"]
    start, end = documents[0]["published_date"], documents[-1]["published_date"]
    paths = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=paths.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=paths.payload_path)
    collection_path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=day, payload_file="announcements.json")
    if cached_miss:
        collection = AnnouncementCollection(version="v1", collected_at="2026-10-27T00:00:00Z",
            announcement_start=start, announcement_end=end, index_counts={symbol: 2},
            documents=(), request_files=())
        FileSystem.write_bytes_atomic(collection_path, collection.model_dump_json().encode())
        meta.commit(pm=pm, payload_path=collection_path)
        output = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
            trade_date=day, payload_file="reviews.json")
        FileSystem.write_bytes_atomic(output.parent / "extraction.json", json.dumps({"inputs": [],
            "issues": {f"{source['ts_code']}/{source['end_date']}/{day}": "missing_implementation"}}).encode())
    index = [{"announcementId": row["announcement_id"], "secCode": row["symbol"], "orgId": "fixture-org",
        "announcementTitle": row["title"],
        "announcementTime": int(datetime.fromisoformat(row["published_date"] + "T00:00:00+08:00").timestamp()) * 1000,
        "adjunctUrl": row["url"].split(".cn/", maxsplit=1)[1]} for row in documents]
    transport = Mock(headers={})
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)

    def get(url: str, **kwargs: object) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response._content = (json.dumps({"stockList": [{"code": symbol, "orgId": "fixture-org"}]}).encode()
            if url.endswith("stock.json") else b"%PDF-transport-fixture")
        return response

    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"totalAnnouncement": 2, "announcements": index}).encode()
    transport.get.side_effect = get
    transport.post.return_value = response
    monkeypatch.setattr("src.data_system.steps.collect_announcements.requests.Session", lambda: transport)
    monkeypatch.setattr("src.data_system.brokers.cninfo.time.sleep", lambda seconds: None)
    monkeypatch.setattr(Access, "trade_dates", lambda self, **kwargs: [day])

    def reader(stream: object) -> SimpleNamespace:
        original = next(row for row in documents if row["announcement_id"] == Path(stream.name).stem)
        return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda page=page: page) for page in original["pages"]])

    monkeypatch.setattr("src.data_system.steps.extract_distribution_terms.PdfReader", reader)
    for _ in range(2):
        _prepare_evidence(path_manager=pm,
            registration_interval=TrainingDateRange(start=day, end=day),
            announcement_interval=TrainingDateRange(start=start, end=end))
        result, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=day)
        assert result.status == "not_entitled"
        assert result.review.terms[0].reference_price is None
        assert result.review.terms[0].reference_cash_per_share is result.review.terms[0].reference_shares_per_share is None
        assert result.review.terms[0].cash_per_share == result.review.terms[0].shares_per_share == 0
        if case_index == 0:
            assert str(result.review.terms[1].reference_price) == "2.52"
        else:
            assert result.review.terms[1].reference_price is None
            assert result.review.terms[1].reference_cash_per_share == result.review.terms[1].reference_shares_per_share == 0
        assert result.review.terms[0].published_date == date.fromisoformat(start)
        assert result.review.terms[1].published_date == date.fromisoformat(end)
        assert result.review.terms[1].published_ts_us is None
    assert transport.post.call_count == 1
    assert len(list(collection_path.parent.glob("*.pdf"))) == 2


def test_workflow_reuses_confirmed_review_with_source_locations(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, supplied, pdf = evidence
    monkeypatch.setattr(Access, "trade_dates", lambda self, **kwargs: list(context.trade_dates))
    registration = TrainingDateRange(start=context.start, end=context.end)
    announcement = TrainingDateRange(start="2024-01-01", end="2026-10-17")
    _prepare_evidence(path_manager=pm, registration_interval=registration,
        announcement_interval=announcement, review_refs=(supplied,))
    resolution, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert resolution.status == "resolved"
    assert str(resolution.review.terms[0].reference_cash_per_share) == "0.28"
    paths = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start)
    before = paths.payload_path.read_bytes(), paths.payload_path.stat().st_mtime_ns
    _prepare_evidence(path_manager=pm, registration_interval=registration,
        announcement_interval=announcement, review_refs=(supplied,))
    assert (paths.payload_path.read_bytes(), paths.payload_path.stat().st_mtime_ns) == before
    assert "source_digest" not in resolution.model_dump()


def test_unmatched_pdf_publishes_specific_gap_and_no_distribution(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, _, _ = evidence
    monkeypatch.setattr(Access, "trade_dates", lambda self, **kwargs: list(context.trade_dates))
    _prepare_evidence(path_manager=pm,
        registration_interval=TrainingDateRange(start=context.start, end=context.end),
        announcement_interval=TrainingDateRange(start="2024-01-01", end="2026-10-17"))
    resolution, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert resolution.status == "unresolved"
    assert resolution.reason == "pdf_text_unreadable:cninfo:1:empty_text"


@pytest.mark.parametrize("independent", [False, True])
def test_publication_discovery_refreshes_registration_and_retains_confirmed_review(
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
        FileSystem.write_bytes_atomic(collection_path, collection.model_dump_json().encode())
        meta.commit(pm=pm, payload_path=collection_path)
        review, = json.loads(supplied.read_bytes())
        review.update(candidate_id="cninfo:1", action_id="cninfo:1:ordinary_shareholder",
            announcement_source=AnnouncementPublication(source="cninfo",
                **original.model_dump(exclude={"sha256", "fetched_at"})).model_dump(mode="json"))
        supplied.write_text(json.dumps([review]))
    _prepare_evidence(path_manager=pm,
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
    report = _refresh_publications(path_manager=pm, publication_interval=publication_interval)
    assert cninfo.company_index.call_count == 1
    assert report.affected_registration_dates == (context.start,)
    assert report.resolution_counts == {"resolved": 1}
    assert report.unmatched_publications == (unmatched,)
    output = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start).payload_path
    before = output.read_bytes(), output.stat().st_mtime_ns
    repeated = _refresh_publications(path_manager=pm, publication_interval=publication_interval)
    assert repeated.output_refs == report.output_refs
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
    previous_id = review["candidate_id"]
    review.update(candidate_id="cninfo:1", action_id="cninfo:1:ordinary_shareholder",
        announcement_source=AnnouncementPublication(source="cninfo",
            **original.model_dump(exclude={"sha256", "fetched_at"})).model_dump(mode="json"))
    supplied.write_text(json.dumps([review]))
    registration = TrainingDateRange(start=context.start, end=context.end)
    announcement = TrainingDateRange(start="2024-01-01", end="2026-10-17")
    for linked in (False, True):
        if linked:
            review["related_sources"] = [previous_id]
            supplied.write_text(json.dumps([review]))
        write_parquet_atomic(output_file=source.payload_path, table=source_table if linked else source_table.slice(0, 0))
        meta.commit(pm=pm, payload_path=source.payload_path)
        FileSystem.write_bytes_atomic(collection_path, collection.model_dump_json().encode())
        meta.commit(pm=pm, payload_path=collection_path)
        _prepare_evidence(path_manager=pm, registration_interval=registration,
            announcement_interval=announcement, review_refs=(supplied,))
        results = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
        assert len(results) == (2 if linked else 1)
        independent, = [row for row in results if row.candidate_id == "cninfo:1"]
        assert independent.review.announcement_source.announcement_id == original.announcement_id
        assert {row.review.action_id for row in results} == {"cninfo:1:ordinary_shareholder"}
        assert pq.ParquetFile(source.payload_path).read().num_rows == (source_table.num_rows if linked else 0)
        output = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start).payload_path
        before = output.read_bytes(), output.stat().st_mtime_ns
        _prepare_evidence(path_manager=pm, registration_interval=registration,
            announcement_interval=announcement)
        assert (output.read_bytes(), output.stat().st_mtime_ns) == before
