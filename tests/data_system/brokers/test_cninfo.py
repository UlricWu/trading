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
    return {"announcementId": str(identity), "secCode": "600000", "announcementTitle": "2026年半年度权益分派实施公告",
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
    (tmp_path / "1.pdf").write_bytes(b"%PDF-changed")
    with pytest.raises(ValueError, match="identity changed"):
        broker.download_distribution_documents(index=index, directory=tmp_path)
