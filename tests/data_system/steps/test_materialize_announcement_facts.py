# filepath: tests/data_system/steps/test_materialize_announcement_facts.py
"""Disclosure-date materialization works without dividend or trading-day partitions."""

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import pyarrow.parquet as pq
from pypdf.errors import PdfReadError
import requests

from src.access import Access, meta
from src.data_system.announcement_publications import AnnouncementPublication, PublicationIndex
from src.data_system.context import DataContext
from src.data_system.steps.materialize_announcement_facts import MaterializeAnnouncementFactsStep
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


def test_incremental_batch_repeat_and_public_observed_time_views_agree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (
        AnnouncementPublication(source="cninfo", announcement_id="1", symbol="600518", title="重整转增实施公告",
            published_date="2026-09-19", url="https://example.test/1.pdf"),
        AnnouncementPublication(source="cninfo", announcement_id="2", symbol="600518", title="转增补充公告",
            published_date="2026-09-20", url="https://example.test/2.pdf"),
    )
    pages = {"1": "证券代码：600518 股权登记日：2026年9月22日。本次不派发现金红利。"
        "1000股股票将向股权登记日登记在册的前十名股东之外的全体股东进行分配，按持股数量的相对比例分配。",
        "2": "证券代码：600518 证券简称：康美。2026年9月19日披露《重整转增实施公告》。普通股东实际每10股获配5股。"
        "除权日：2026年9月23日。转增股本上市日：2026年9月24日。"}
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    response = Mock(content=b"%PDF-original")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    session.get.return_value = response
    monkeypatch.setattr("src.data_system.steps.materialize_announcement_facts.requests.Session", lambda: session)
    monkeypatch.setattr("src.data_system.steps.materialize_announcement_facts.PdfReader",
        lambda stream: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: pages[Path(stream.name).parent.name])]))
    observed = datetime(2026, 10, 28, tzinfo=timezone.utc)
    outputs = []
    for mode in ("batch", "incremental"):
        (tmp_path / mode).mkdir()
        pm = PathManager(tmp_path / mode)
        step = MaterializeAnnouncementFactsStep(pm=pm, clock=lambda: observed)
        for row in rows:
            path = pm.raw_payload(broker="cninfo", source_name="corporate_action_publications",
                trade_date=row.published_date.isoformat(), payload_file="index.json")
            index = PublicationIndex(version="v1", source="cninfo", publication_date=row.published_date, announcements=(row,))
            FileSystem.write_bytes_atomic(path, index.model_dump_json().encode())
            meta.commit(pm=pm, payload_path=path)
            if mode == "incremental":
                step.run(DataContext(start=row.published_date.isoformat(), end=row.published_date.isoformat()))
        whole = DataContext(start="2026-09-19", end="2026-09-20")
        step.run(whole)
        access = Access(pm=pm, processed_version="v1")
        before = int(datetime.fromisoformat("2026-09-19T23:59:59+08:00").timestamp()) * 1_000_000
        early = int(datetime.fromisoformat("2026-09-20T00:00:00+08:00").timestamp()) * 1_000_000
        final = int(datetime.fromisoformat("2026-09-21T00:00:00+08:00").timestamp()) * 1_000_000
        assert access.announcement_events(as_of_ts_us=before) == ()
        first, = access.announcement_events(as_of_ts_us=early)
        last, = access.announcement_events(as_of_ts_us=final)
        assert first.shares_numerator == 1000 and first.shares_per_share is None
        assert last.event_id == first.event_id and last.shares_per_share == .5
        assert last.record_date == date(2026, 9, 22) and last.shares_available_date == date(2026, 9, 24)
        assert access.announcement_events(as_of_ts_us=final, observed_by_ts_us=final) == ()
        assert access.announcement_events(as_of_ts_us=final,
            observed_by_ts_us=int(observed.timestamp()) * 1_000_000) == (last,)
        assert access.announcement_facts(publication_date="2026-09-18", as_of_ts_us=final) is None
        output = pm.processed_object(dataset_name="announcement_facts", version="v1", trade_date="2026-09-20").payload_path
        unchanged = (output.read_bytes(), output.stat().st_mtime_ns)
        step.run(whole)
        assert unchanged == (output.read_bytes(), output.stat().st_mtime_ns)
        assert pq.ParquetFile(output).schema_arrow.names[:3] == ["event_id", "symbol", "published_date"]
        outputs.append(access.announcement_history(as_of_ts_us=final))
    assert outputs[0] == outputs[1]
    assert session.get.call_count == 4


def test_date_only_supplement_links_to_an_earlier_intraday_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exact = int(datetime.fromisoformat("2026-09-19T12:00:00+08:00").timestamp()) * 1_000_000
    early = AnnouncementPublication(source="cninfo", announcement_id="9", symbol="600518", title="重整转增实施公告",
        published_date="2026-09-19", published_ts_us=exact, url="https://example.test/9.pdf")
    supplement = early.model_copy(update={"announcement_id": "1", "published_ts_us": None,
        "title": "转增补充公告", "url": "https://example.test/1.pdf"})
    pages = {"9": "证券代码：600518 股权登记日：2026年9月22日。本次不派发现金红利。",
        "1": "证券代码：600518 证券简称：康美。2026年9月19日披露《重整转增实施公告》。普通股东实际每10股获配5股。"}
    session = Mock(headers={})
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    response = requests.Response()
    response.status_code = 200
    response._content = b"%PDF-original"
    session.get.return_value = response
    monkeypatch.setattr("src.data_system.steps.materialize_announcement_facts.requests.Session", lambda: session)
    monkeypatch.setattr("src.data_system.brokers.cninfo.time.sleep", lambda seconds: None)
    monkeypatch.setattr("src.data_system.steps.materialize_announcement_facts.PdfReader",
        lambda stream: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: pages[Path(stream.name).parent.name])]))
    pm = PathManager(tmp_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_publications",
        trade_date="2026-09-19", payload_file="index.json")
    index = PublicationIndex(version="v1", source="cninfo", publication_date="2026-09-19", announcements=(supplement, early))
    FileSystem.write_bytes_atomic(path, index.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    MaterializeAnnouncementFactsStep(pm=pm, clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc)).run(
        DataContext(start="2026-09-19", end="2026-09-19"))
    access = Access(pm=pm, processed_version="v1")
    before, = access.announcement_events(as_of_ts_us=exact)
    after, = access.announcement_events(as_of_ts_us=supplement.available_ts_us)
    assert before.event_id == after.event_id == "cninfo:9"
    assert before.shares_per_share is None and after.shares_per_share == .5
    assert after.cash_per_share == 0 and after.record_date == before.record_date


@pytest.mark.parametrize("failure", ["mirror_denial", "security_denial", "unreadable_pdf"])
def test_publication_materialization_preserves_download_and_parse_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    publication = AnnouncementPublication(source="bse", announcement_id="a" * 32, symbol="430685",
        title="重整进展公告", published_date="2026-09-19",
        url="https://www.bse.cn/disclosure/2026/2026-09-19/original.pdf")
    pm = PathManager(tmp_path)
    index_path = pm.raw_payload(broker="bse", source_name="corporate_action_publications",
        trade_date="2026-09-19", payload_file="index.json")
    index = PublicationIndex(version="v1", source="bse", publication_date="2026-09-19", announcements=(publication,))
    FileSystem.write_bytes_atomic(index_path, index.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=index_path)
    original = requests.Response()
    original.status_code = 200
    original._content = b"%PDF-original"
    denied = requests.Response()
    denied.status_code = 403
    denied._content = "云安全平台".encode() if failure == "security_denial" else b"mirror denied"
    session = Mock(headers={})
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.side_effect = [original] if failure == "unreadable_pdf" else [denied, original]
    monkeypatch.setattr("src.data_system.steps.materialize_announcement_facts.requests.Session", lambda: session)
    monkeypatch.setattr("src.data_system.brokers.bse.time.sleep", lambda seconds: None)
    reader = Mock(side_effect=PdfReadError("unreadable") if failure == "unreadable_pdf" else None,
        return_value=SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "证券代码：430685 重整方案尚需批准。")]))
    monkeypatch.setattr("src.data_system.steps.materialize_announcement_facts.PdfReader", reader)
    step = MaterializeAnnouncementFactsStep(pm=pm, clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc))
    context = DataContext(start="2026-09-19", end="2026-09-19")
    output = pm.processed_object(dataset_name="announcement_facts", version="v1", trade_date="2026-09-19")
    if failure == "security_denial":
        with pytest.raises(requests.HTTPError):
            step.run(context)
        assert not output.meta_path.exists() and session.get.call_count == 1
        assert len(list(index_path.parent.glob("documents/*/download-attempt-*.response"))) == 1
        return
    step.run(context)
    facts, = Access(pm=pm, processed_version="v1").announcement_facts(
        publication_date="2026-09-19", as_of_ts_us=publication.available_ts_us)
    assert facts.shares_per_share is None
    if failure == "unreadable_pdf":
        assert facts.parse_issue == "pdf_text_unreadable:PdfReadError"
        assert not list(index_path.parent.glob("documents/*/text.json"))
    else:
        assert facts.benefit_state == "conditional" and facts.parse_issue is None
        assert session.get.call_args.args[0].startswith("https://www.neeq.com.cn/")
        assert session.get.call_count == 2
