# filepath: tests/data_system/brokers/test_bse.py
"""Verify official historical security identities and complete BSE disclosure pages."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from src.data_system.brokers.bse import BseBroker


def _response(payload: bytes) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response._content = payload
    return response


def _row(identity: int, symbol: str = "430685") -> dict[str, object]:
    return {"companyCd": symbol, "disclosureCode": f"{identity:032x}",
        "disclosureTitle": "[临时公告]新芝生物:2018年年度权益分派实施公告",
        "publishDate": "2019-05-13", "destFilePath": f"/disclosure/2019/2019-05-13/1557737525_{identity}.pdf"}


def _page(rows: list[dict[str, object]], *, number: int, total: int, size: int = 2) -> requests.Response:
    payload = [{"status": 0, "listInfo": {"number": number, "totalElements": total,
        "totalPages": (total + size - 1) // size, "size": size, "numberOfElements": len(rows), "content": rows}}]
    return _response(("null(" + json.dumps(payload) + ")").encode())


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr("src.data_system.brokers.bse.time.sleep", lambda seconds: None)
    return Mock(spec=requests.Session, headers={})


def test_code_mapping_uses_official_pairs_not_numeric_suffixes(transport: Mock, tmp_path: Path) -> None:
    transport.get.return_value = _response("""<table><tr><td>序号</td><td>证券简称</td>
        <td>上市日期</td><td>旧代码</td><td>新代码</td></tr>
        <tr><td>1</td><td>芭薇股份</td><td>2024/3/29</td><td>837023</td><td>920123</td></tr>
        <tr><td>2</td><td>新芝生物</td><td>2022/10/10</td><td>430685</td><td>920685</td></tr>
        <tr><td colspan="5">注：由全国股转系统原精选层平移进入北交所的上市公司，上表中上市日期字段为其在原精选层的挂牌日期。</td></tr></table>""".encode())
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    identities = broker.security_identities(directory=tmp_path)
    assert {(row.symbol, row.published_symbol) for row in identities} == {("920123", "837023"), ("920685", "430685")}
    assert all(row.evidence_url == "https://www.bse.cn/service/code_mapping.html" for row in identities)
    assert (tmp_path / "security-code-mapping.html").is_file()


def test_pagination_honors_returned_size_and_preserves_native_identity(transport: Mock, tmp_path: Path) -> None:
    transport.post.side_effect = [_page([_row(1), _row(2)], number=0, total=3),
                                  _page([_row(3)], number=1, total=3)]
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    rows = broker.company_index(symbol="430685", start=date(2019, 5, 1), end=date(2019, 5, 31), directory=tmp_path)
    assert [row.announcement_id for row in rows] == [f"{n:032x}" for n in (1, 2, 3)]
    assert [call.kwargs["data"]["page"] for call in transport.post.call_args_list] == [0, 1]
    assert len(list(tmp_path.glob("query-*.request.json"))) == 2
    transport.get.return_value = _response(b"%PDF-original-BSE-evidence")
    documents = broker.download_distribution_documents(index=rows, directory=tmp_path)
    assert documents[0].symbol == "430685"
    assert documents[0].url == "https://www.bse.cn/disclosure/2019/2019-05-13/1557737525_1.pdf"
    assert broker.download_distribution_documents(index=rows, directory=tmp_path) == documents
    assert transport.get.call_count == 3
    assert (tmp_path / f"{documents[0].announcement_id}.pdf").read_bytes() == b"%PDF-original-BSE-evidence"
    assert documents[0].sha256 is None
    assert transport.get.call_count == 3


def test_source_implementation_date_keeps_generic_titles_for_body_matching(transport: Mock, tmp_path: Path) -> None:
    row = {**_row(1), "disclosureTitle": "[临时公告]新芝生物:提示性公告"}
    transport.post.return_value = _page([row], number=0, total=1)
    transport.get.return_value = _response(b"%PDF-body-matching-fixture")
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 27, tzinfo=timezone.utc))
    index = broker.company_index(symbol="430685", start=date(2019, 5, 13), end=date(2019, 5, 13), directory=tmp_path)
    assert broker.download_distribution_documents(index=index, directory=tmp_path) == ()
    document, = broker.download_distribution_documents(index=index, directory=tmp_path,
        implementation_dates=frozenset({date(2019, 5, 13)}))
    assert document.published_date == date(2019, 5, 13)
    assert transport.get.call_count == 1


def test_manual_source_identity_and_legacy_upload_path_are_preserved(transport: Mock, tmp_path: Path) -> None:
    original = {**_row(1, "830799"), "disclosureCode": "manual10181", "publishDate": "2021-09-03",
        "disclosureTitle": "[临时公告]艾融软件：股东增持股份计划公告",
        "destFilePath": "/uploads/1/file/public/202109/20210903125224_x4ksxwfg0w.pdf"}
    transport.post.return_value = _page([original], number=0, total=1)
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    publication, = broker.announcement_index(start=date(2021, 9, 3), end=date(2021, 9, 3), directory=tmp_path)
    assert publication.announcement_id == "manual10181"
    assert publication.url == "https://www.bse.cn" + original["destFilePath"]


@pytest.mark.parametrize("failure", ["overlap", "outside_symbol", "changed_total"])
def test_partial_or_changed_index_never_becomes_success(
    transport: Mock, tmp_path: Path, failure: str,
) -> None:
    second = _row(2 if failure == "overlap" else 3, "920685" if failure == "outside_symbol" else "430685")
    transport.post.side_effect = [_page([_row(1), _row(2)], number=0, total=3),
        _page([second], number=1, total=4 if failure == "changed_total" else 3)]
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="incomplete|outside query|changed"):
        broker.company_index(symbol="430685", start=date(2019, 5, 1), end=date(2019, 5, 31), directory=tmp_path)


def test_missing_mapping_table_and_corrupt_pdf_fail(transport: Mock, tmp_path: Path) -> None:
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    transport.get.return_value = _response(b"<html>permission denied</html>")
    with pytest.raises(ValueError, match="code mapping"):
        broker.security_identities(directory=tmp_path)
    transport.post.return_value = _page([_row(1)], number=0, total=1)
    rows = broker.company_index(symbol="430685", start=date(2019, 5, 1), end=date(2019, 5, 31), directory=tmp_path)
    with pytest.raises(ValueError, match="not a PDF"):
        broker.download_distribution_documents(index=rows, directory=tmp_path)


def test_company_query_includes_selected_tier_and_bse_market_not_only_neeq(
    transport: Mock, tmp_path: Path,
) -> None:
    def query(url: str, *, data: dict[str, object], timeout: int) -> requests.Response:
        rows = [_row(1)] if data["isNewThree"] == "" else []
        return _page(rows, number=0, total=len(rows))

    transport.post.side_effect = query
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    rows = broker.company_index(symbol="430685", start=date(2019, 5, 1), end=date(2019, 5, 31), directory=tmp_path)
    assert len(rows) == 1


def test_current_hexadecimal_pdf_paths_keep_the_original_source_url(transport: Mock, tmp_path: Path) -> None:
    path = "/disclosure/2019/2019-05-13/a312e0f501494f9180d8195f4d4cf06d.pdf"
    transport.post.return_value = _page([{**_row(1), "destFilePath": path}], number=0, total=1)
    transport.get.return_value = _response(b"%PDF-original")
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    rows = broker.company_index(symbol="430685", start=date(2019, 5, 1), end=date(2019, 5, 31), directory=tmp_path)
    document, = broker.download_distribution_documents(index=rows, directory=tmp_path)
    assert document.url == "https://www.bse.cn" + path


def test_official_neeq_original_can_resolve_a_bse_mirror_denial(transport: Mock, tmp_path: Path) -> None:
    transport.post.return_value = _page([_row(1)], number=0, total=1)
    denied = _response(b"mirror denied")
    denied.status_code = 403
    transport.get.side_effect = [denied, _response(b"%PDF-NEEQ-original")]
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    rows = broker.company_index(symbol="430685", start=date(2019, 5, 1), end=date(2019, 5, 31), directory=tmp_path)
    document, = broker.download_distribution_documents(index=rows, directory=tmp_path)
    assert document.url.startswith("https://www.neeq.com.cn/disclosure/")
    assert broker.download_distribution_documents(index=rows, directory=tmp_path) == (document,)
    assert transport.get.call_count == 2
    attempts = [json.loads(p.read_bytes()) for p in tmp_path.glob("download-attempt-*.json")]
    assert any(item["http_status"] == 403 and item["url"].startswith("https://www.bse.cn/") for item in attempts)
    assert any(item["http_status"] == 200 and item["url"] == document.url for item in attempts)


def test_cloud_security_denial_is_preserved_and_does_not_switch_mirrors(transport: Mock, tmp_path: Path) -> None:
    transport.post.return_value = _page([_row(1)], number=0, total=1)
    denied = _response("云安全平台检测到您当前的访问行为存在异常，请稍后重试".encode())
    denied.status_code = 403
    transport.get.side_effect = [denied, _response(b"%PDF-must-not-be-requested")]
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    rows = broker.company_index(symbol="430685", start=date(2019, 5, 1), end=date(2019, 5, 31), directory=tmp_path)
    with pytest.raises(requests.HTTPError):
        broker.download_distribution_documents(index=rows, directory=tmp_path)
    assert transport.get.call_count == 1
    response, = tmp_path.glob("download-attempt-*.response")
    assert response.read_bytes() == denied.content


def test_publication_discovery_keeps_cross_company_rows(transport: Mock, tmp_path: Path) -> None:
    transport.post.return_value = _page([_row(1), _row(2, "836395")], number=0, total=2)
    broker = BseBroker(session=transport, clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    rows = broker.announcement_index(start=date(2019, 5, 13), end=date(2019, 5, 13), directory=tmp_path)
    assert {row.symbol for row in rows} == {"430685", "836395"}
    assert all(row.source == "bse" for row in rows)
    assert transport.post.call_args.kwargs["data"]["companyCd"] == ""
