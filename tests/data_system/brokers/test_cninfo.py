# filepath: tests/data_system/brokers/test_cninfo.py
"""Validate announcement source completeness and immutable original identity."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from src.data_system.brokers.cninfo import CninfoBroker


def _response(payload: object) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(payload).encode()
    return response


def _row(identity: int, day: int = 1) -> dict[str, object]:
    return {"announcementId": str(identity), "secCode": "600000", "orgId": "gssh0600000", "announcementTitle": "2026年半年度权益分派实施公告",
            "announcementTime": int(datetime(2026, 9, day, tzinfo=timezone.utc).timestamp() * 1000),
            "adjunctUrl": f"finalpage/2026-09-{day:02d}/{identity}.PDF"}


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr("src.data_system.brokers.cninfo.time.sleep", lambda seconds: None)
    return Mock(spec=requests.Session, headers={})


def test_overlap_splits_range_and_checks_unique_total(transport: Mock, tmp_path: Path) -> None:
    rows = [_row(i) for i in range(1, 31)] + [_row(31, 2)]
    transport.post.side_effect = [_response({"totalAnnouncement": 31, "announcements": rows[:30]}),
        _response({"totalAnnouncement": 31, "announcements": rows[:1]}),
        _response({"totalAnnouncement": 30, "announcements": rows[:30]}),
        _response({"totalAnnouncement": 1, "announcements": rows[30:]})]
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    index = broker.company_index(symbol="600000", org_id="gssh0600000", start=date(2026, 9, 1),
        end=date(2026, 9, 2), directory=tmp_path)
    assert len(index) == len({row.announcement_id for row in index}) == 31
    assert len(list(tmp_path.glob("*.request.json"))) == 4


def test_unrecoverable_truncation_is_an_error(transport: Mock, tmp_path: Path) -> None:
    transport.post.side_effect = [_response({"totalAnnouncement": 2, "announcements": [_row(1)]}) for _ in range(2)]
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="index incomplete"):
        broker.company_index(symbol="600000", org_id="gssh0600000", start=date(2026, 9, 1),
                             end=date(2026, 9, 1), directory=tmp_path)


@pytest.mark.parametrize("status,recovered", [(504, True), (403, False)])
def test_transient_page_failure_retries_but_access_denial_does_not(
    transport: Mock, tmp_path: Path, status: int, recovered: bool,
) -> None:
    failed = _response({"failure": status})
    failed.status_code = status
    transport.post.side_effect = [failed, _response({"totalAnnouncement": 1, "announcements": [_row(1)]})]
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    if recovered:
        assert len(broker.announcement_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)) == 1
    else:
        with pytest.raises(requests.HTTPError):
            broker.announcement_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)
    assert transport.post.call_count == (2 if recovered else 1)
    assert len(list(tmp_path.glob("*.request.json"))) == transport.post.call_count
    receipts = [json.loads(path.read_bytes()) for path in tmp_path.glob("*.request.json")]
    assert status in {row["http_status"] for row in receipts}


def test_conflicting_rows_with_one_announcement_id_are_not_overwritten(transport: Mock, tmp_path: Path) -> None:
    first = [_row(i) for i in range(1, 31)]
    changed = {**first[0], "announcementTitle": "changed title"}
    transport.post.side_effect = [_response({"totalAnnouncement": 31, "announcements": first}),
        _response({"totalAnnouncement": 31, "announcements": [changed, _row(31)]})]
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="identity changed during collection"):
        broker.company_index(symbol="600000", org_id="gssh0600000", start=date(2026, 9, 1),
                             end=date(2026, 9, 1), directory=tmp_path)


def test_pdf_cache_preserves_collection_time_and_rejects_mutation(transport: Mock, tmp_path: Path) -> None:
    transport.post.return_value = _response({"totalAnnouncement": 1, "announcements": [_row(1)]})
    response = _response(None)
    response._content = b"%PDF-original-fact"
    transport.get.return_value = response
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    index = broker.company_index(symbol="600000", org_id="gssh0600000", start=date(2026, 9, 1),
                                 end=date(2026, 9, 1), directory=tmp_path)
    documents = broker.download_distribution_documents(index=index, directory=tmp_path)
    assert broker.download_distribution_documents(index=index, directory=tmp_path) == documents
    assert transport.get.call_count == 1
    (tmp_path / "1.pdf").write_bytes(b"%PDF-modified-fact")
    with pytest.raises(ValueError, match="identity changed"):
        broker.download_distribution_documents(index=index, directory=tmp_path)


@pytest.mark.parametrize("local_time,precise", [("07:41:10", True), ("00:00:00", False)])
def test_publication_time_preserves_index_instant_but_does_not_claim_midnight_precision(
    transport: Mock, tmp_path: Path, local_time: str, precise: bool,
) -> None:
    stamp = int(datetime.fromisoformat(f"2026-09-01T{local_time}+08:00").timestamp()) * 1000
    source = {**_row(1), "announcementTime": stamp}
    transport.post.return_value = _response({"totalAnnouncement": 1, "announcements": [source]})
    response = _response(None)
    response._content = b"%PDF-fixed-test-content"
    transport.get.return_value = response
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    publication, = broker.announcement_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)
    rows = broker.company_index(symbol="600000", org_id="gssh0600000", start=date(2026, 9, 1),
        end=date(2026, 9, 1), directory=tmp_path)
    document, = broker.download_distribution_documents(index=rows, directory=tmp_path)
    expected = stamp * 1000 if precise else None
    assert publication.published_ts_us == document.published_ts_us == expected
    assert publication.published_date == document.published_date == date(2026, 9, 1)


def test_distribution_index_preserves_cross_company_rows_and_query_evidence(transport: Mock, tmp_path: Path) -> None:
    rows = [_row(1), {**_row(2), "secCode": "920879"}]
    transport.post.return_value = _response({"totalAnnouncement": 2, "announcements": rows})
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    index = broker.distribution_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)
    assert [(row.announcement_id, row.symbol) for row in index] == [("1", "600000"), ("2", "920879")]
    request, = tmp_path.glob('*.request.json')
    parameters = json.loads(request.read_bytes())['parameters']
    assert parameters['stock'] == ''
    assert parameters['category'] == 'category_qyfpxzcs_szsh'


def test_distribution_index_rejects_outside_date_and_truncated_results(transport: Mock, tmp_path: Path) -> None:
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    transport.post.return_value = _response({"totalAnnouncement": 1, "announcements": [_row(1, 2)]})
    with pytest.raises(ValueError, match='outside query'):
        broker.distribution_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)
    transport.post.return_value = _response({"totalAnnouncement": 2, "announcements": [_row(1)]})
    with pytest.raises(ValueError, match='index incomplete'):
        broker.distribution_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)


def test_single_day_market_overlap_is_completed_using_disjoint_public_boards(transport: Mock, tmp_path: Path) -> None:
    sh = _row(1)
    sz = {**_row(2), 'secCode': '000001'}
    transport.post.side_effect = [
        _response({'totalAnnouncement': 2, 'announcements': [sh]}),
        _response({'totalAnnouncement': 2, 'announcements': [sh]}),
        _response({'totalAnnouncement': 1, 'announcements': [sz]}),
        _response({'totalAnnouncement': 1, 'announcements': [sh]}),
        _response({'totalAnnouncement': 0, 'announcements': None}),
    ]
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    index = broker.distribution_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)
    assert {row.announcement_id for row in index} == {'1', '2'}
    assert [call.kwargs['data']['plate'] for call in transport.post.call_args_list] == ['', '', 'sz', 'sh', 'bj']


def test_terminal_board_overlap_is_completed_from_observed_source_issuers(transport: Mock, tmp_path: Path) -> None:
    rows = [_row(1), _row(2)]

    def query(url: str, *, data: dict[str, object], timeout: int) -> requests.Response:
        if data["stock"]:
            assert data["stock"] == "600000,gssh0600000"
            return _response({"totalAnnouncement": 2, "announcements": rows})
        if data["plate"] in ("sz", "szmb", "szcy", "bj", "shkcp"):
            return _response({"totalAnnouncement": 0, "announcements": None})
        return _response({"totalAnnouncement": 2, "announcements": rows[:1]})

    transport.post.side_effect = query
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    index = broker.announcement_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)
    assert {row.announcement_id for row in index} == {"1", "2"}
    company_queries = [call.kwargs["data"] for call in transport.post.call_args_list if call.kwargs["data"]["stock"]]
    assert len(company_queries) == 1
    assert company_queries[0]["plate"] == "shmb"


@pytest.mark.parametrize("total,complete", [(2, True), (3, False)])
def test_parent_and_board_observations_can_complement_each_other_only_to_the_declared_total(
    transport: Mock, tmp_path: Path, total: int, complete: bool,
) -> None:
    first = _row(1)
    second = {**_row(2), "secCode": "600001", "orgId": "gssh0600001"}

    def query(url: str, *, data: dict[str, object], timeout: int) -> requests.Response:
        if data["stock"]:
            row = first if str(data["stock"]).startswith("600000,") else second
            return _response({"totalAnnouncement": 1, "announcements": [row]})
        if data["plate"] in ("sz", "szmb", "szcy", "bj", "shkcp"):
            return _response({"totalAnnouncement": 0, "announcements": None})
        row = first if data["plate"] else second
        return _response({"totalAnnouncement": total, "announcements": [row]})

    transport.post.side_effect = query
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    if complete:
        rows = broker.announcement_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)
        assert {row.announcement_id for row in rows} == {"1", "2"}
    else:
        with pytest.raises(ValueError, match="index incomplete"):
            broker.announcement_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)


def test_verified_old_code_is_allowed_without_accepting_unrelated_issuers(transport: Mock, tmp_path: Path) -> None:
    transport.post.return_value = _response({"totalAnnouncement": 1, "announcements": [{**_row(1), "secCode": "000043"}]})
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="outside query"):
        broker.company_index(symbol="001914", org_id="gssz0000043", start=date(2026, 9, 1),
            end=date(2026, 9, 1), directory=tmp_path)
    rows = broker.company_index(symbol="001914", org_id="gssz0000043", start=date(2026, 9, 1),
        end=date(2026, 9, 1), directory=tmp_path, historical_symbols=("000043",))
    assert rows[0].symbol == "000043"
    with pytest.raises(ValueError, match="outside query"):
        broker.company_index(symbol="001914", org_id="gssz0000043", start=date(2026, 9, 1),
            end=date(2026, 9, 1), directory=tmp_path, historical_symbols=("000022",))


def test_publication_discovery_does_not_limit_category_or_known_company(transport: Mock, tmp_path: Path) -> None:
    row = {**_row(1), "announcementTitle": "关于相关公告更正的说明"}
    transport.post.return_value = _response({"totalAnnouncement": 1, "announcements": [row]})
    broker = CninfoBroker(session=transport, clock=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc))
    publication, = broker.announcement_index(start=date(2026, 9, 1), end=date(2026, 9, 1), directory=tmp_path)
    assert publication.source == "cninfo"
    assert publication.announcement_id == "1"
    assert publication.title == row["announcementTitle"]
    parameters = transport.post.call_args.kwargs["data"]
    assert parameters["stock"] == parameters["category"] == ""
