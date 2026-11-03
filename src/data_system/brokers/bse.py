# filepath: src/data_system/brokers/bse.py
"""Read BSE's official historical NEEQ disclosures and security-code mapping."""

from collections.abc import Callable, Collection, Sequence
from datetime import date, datetime
from html import unescape
import json
from pathlib import Path
from uuid import uuid4
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
import requests

from src.data_system.announcement_evidence import AnnouncementFile, SecurityIdentity
from src.data_system.announcement_publications import AnnouncementPublication, is_distribution_evidence
from src.utils.filesystem import FileSystem


class _IndexRow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    announcement_id: str = Field(alias="disclosureCode", pattern=r"^(?:[a-f0-9]{32}|manual[0-9]+)$")
    symbol: str = Field(alias="companyCd", pattern=r"^[0-9]{6}$")
    title: str = Field(alias="disclosureTitle")
    published_date: date = Field(alias="publishDate")
    path: str = Field(alias="destFilePath", pattern=r"^/(?:disclosure/\d{4}/\d{4}-\d{2}-\d{2}/[a-fA-F0-9_]+"
        r"|uploads/[0-9]+/file/public/[0-9]{6}/[0-9]+_[a-zA-Z0-9]+)\.[Pp][Dd][Ff]$")


class _IndexPage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    number: int = Field(ge=0)
    size: int = Field(gt=0)
    count: int = Field(alias="numberOfElements", ge=0)
    total: int = Field(alias="totalElements", ge=0)
    pages: int = Field(alias="totalPages", ge=0)
    rows: tuple[_IndexRow, ...] = Field(alias="content")


class _IndexResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    status: Literal[0]
    page: _IndexPage = Field(alias="listInfo")


class BseBroker:
    """Collect primary historical disclosure evidence with a caller-owned session.

    Example:
        with requests.Session() as session:
            broker = BseBroker(session=session, clock=utc_now)
            rows = broker.company_index(symbol="430685", start=date(2019, 5, 1),
                end=date(2019, 5, 31), directory=raw_directory)
    """

    def __init__(self, *, session: requests.Session, clock: Callable[[], datetime]) -> None:
        """Bind transport and observation time without fetching data.

        Example:
            broker = BseBroker(session=session, clock=utc_now)
        """
        self._session = session
        self._clock = clock
        self._session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.bse.cn/disclosure/announcement.html"})

    def security_identities(self, *, directory: Path) -> tuple[SecurityIdentity, ...]:
        """Preserve official old/new pairs without inferring identities from code suffixes.

        Example:
            aliases = broker.security_identities(directory=raw_directory)
        """
        url = "https://www.bse.cn/service/code_mapping.html"
        with self._session.get(url, timeout=30) as response:
            response.raise_for_status()
            payload = response.content
        FileSystem.write_bytes_atomic(directory / "security-code-mapping.html", payload)
        FileSystem.write_bytes_atomic(directory / "security-code-mapping.request.json", json.dumps({
            "url": url, "collected_at": self._clock().isoformat(),
        }, sort_keys=True).encode())
        pairs = []
        tables = re.findall(r"<table\b[^>]*>(.*?)</table>", payload.decode("utf-8"), flags=re.S | re.I)
        for table in tables:
            rows = [[re.sub(r"\s+", "", unescape(re.sub(r"<[^>]*>", "", cell)))
                     for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", row, flags=re.S | re.I)]
                    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, flags=re.S | re.I)]
            if not rows or rows[0] != ["序号", "证券简称", "上市日期", "旧代码", "新代码"]:
                continue
            for row in rows[1:]:
                if len(row) == 1 and row[0].startswith("注："):
                    continue
                if len(row) != 5 or not re.fullmatch(r"[0-9]{6}", row[3]) or not re.fullmatch(r"[0-9]{6}", row[4]):
                    raise ValueError("BSE code mapping contains an invalid security row")
                pairs.append(SecurityIdentity(symbol=row[4], published_symbol=row[3],
                    evidence_url=url, evidence_file="security-code-mapping.html"))
        if not pairs or len({row.symbol for row in pairs}) != len(pairs) or len({row.published_symbol for row in pairs}) != len(pairs):
            raise ValueError("BSE code mapping missing or ambiguous")
        return tuple(pairs)

    def company_index(
        self, *, symbol: str, start: date, end: date, directory: Path,
    ) -> tuple[_IndexRow, ...]:
        """Verify every native page, company identity, date and unique disclosure count.

        Example:
            rows = broker.company_index(symbol="430685", start=date(2019, 5, 1),
                end=date(2019, 5, 31), directory=raw_directory)
        """
        rows = self._index(symbol=symbol, start=start, end=end, directory=directory)
        if any(row.symbol != symbol for row in rows):
            raise ValueError(f"BSE announcement outside query: {symbol}/{start}/{end}")
        return rows

    def announcement_index(self, *, start: date, end: date, directory: Path) -> tuple[AnnouncementPublication, ...]:
        """Discover all company publications without restricting current or historical market tiers.

        Example:
            rows = broker.announcement_index(start=date(2026, 9, 19), end=date(2026, 9, 19), directory=raw_directory)
        """
        rows = self._index(symbol="", start=start, end=end, directory=directory)
        return tuple(AnnouncementPublication(source="bse", announcement_id=row.announcement_id,
            symbol=row.symbol, title=row.title, published_date=row.published_date, url="https://www.bse.cn" + row.path)
            for row in rows)

    def _index(self, *, symbol: str, start: date, end: date, directory: Path) -> tuple[_IndexRow, ...]:
        url = "https://www.bse.cn/disclosureInfoController/infoResult.do"
        first = None
        unique: dict[str, _IndexRow] = {}
        page_number = 0
        while first is None or page_number < first.pages:
            parameters = {"disclosureType": "5", "page": page_number, "companyCd": symbol,
                "isNewThree": "", "startTime": start.isoformat(), "endTime": end.isoformat(),
                "keyword": "", "xxfcbj": ""}
            with self._session.post(url, data=parameters, timeout=30) as response:
                payload = response.content
                collected_at = self._clock().isoformat()
                identity = uuid4().hex
                FileSystem.write_bytes_atomic(directory / f"query-{identity}.json", payload)
                FileSystem.write_bytes_atomic(directory / f"query-{identity}.request.json", json.dumps({
                    "url": url, "parameters": parameters, "collected_at": collected_at,
                    "http_status": response.status_code,
                }, ensure_ascii=False, sort_keys=True).encode())
                response.raise_for_status()
            envelope = re.fullmatch(rb"\s*null\((.*)\)\s*;?\s*", payload, flags=re.S)
            if envelope is None:
                raise ValueError("BSE announcement response lacks its JSONP envelope")
            parsed, = TypeAdapter(tuple[_IndexResponse]).validate_json(envelope[1])
            page = parsed.page
            if (page.number != page_number or page.count != len(page.rows)
                or page.pages != (page.total + page.size - 1) // page.size):
                raise ValueError(f"BSE announcement page inconsistent: {symbol}/{page_number}")
            if first is None:
                first = page
            elif (page.total, page.pages, page.size) != (first.total, first.pages, first.size):
                raise ValueError(f"BSE announcement index changed: {symbol}/{start}/{end}")
            for row in page.rows:
                if not start <= row.published_date <= end:
                    raise ValueError(f"BSE announcement outside query: {row.announcement_id}")
                if row.announcement_id in unique and unique[row.announcement_id] != row:
                    raise ValueError(f"BSE announcement identity changed: {row.announcement_id}")
                unique[row.announcement_id] = row
            page_number += 1
            time.sleep(0.5)
        if len(unique) != first.total:
            raise ValueError(f"BSE announcement index incomplete: {symbol} expected={first.total} actual={len(unique)}")
        return tuple(sorted(unique.values(), key=lambda row: (row.published_date, row.announcement_id)))

    def download_distribution_documents(
        self, *, index: Sequence[_IndexRow], directory: Path, implementation_dates: Collection[date] = (),
    ) -> tuple[AnnouncementFile, ...]:
        """Keep original distribution PDFs and corrections with download receipts.

        Example:
            documents = broker.download_distribution_documents(index=rows, directory=raw_directory,
                implementation_dates=(date(2019, 5, 13),))
        """
        documents = []
        for row in index:
            if not is_distribution_evidence(row.title) and row.published_date not in implementation_dates:
                continue
            publication = AnnouncementPublication(source="bse", announcement_id=row.announcement_id,
                symbol=row.symbol, title=row.title, published_date=row.published_date,
                url="https://www.bse.cn" + row.path)
            documents.append(self.download_announcement(publication=publication,
                path=directory / f"{row.announcement_id}.pdf"))
        return tuple(documents)

    def download_announcement(self, *, publication: AnnouncementPublication, path: Path) -> AnnouncementFile:
        """Preserve an original PDF and receipt, including the official historical mirror.

        Example:
            original = broker.download_announcement(publication=publication, path=pdf_path)
        """
        urls = (publication.url, publication.url.replace("https://www.bse.cn/", "https://www.neeq.com.cn/", 1))
        receipt = path.with_suffix(".download.json")
        if path.exists():
            saved = AnnouncementFile.model_validate_json(receipt.read_bytes())
            if saved.url not in urls:
                raise ValueError(f"cached BSE announcement identity changed: {publication.announcement_id}")
            return saved
        for url in urls:
            with self._session.get(url, timeout=40) as response:
                payload = response.content
                fetched_at = self._clock()
                attempt = uuid4().hex
                FileSystem.write_bytes_atomic(path.parent / f"download-attempt-{attempt}.json", json.dumps({
                    "url": url, "fetched_at": fetched_at.isoformat(), "http_status": response.status_code,
                }, sort_keys=True).encode())
                if response.status_code >= 400:
                    FileSystem.write_bytes_atomic(path.parent / f"download-attempt-{attempt}.response", payload)
                if response.status_code == 403 and ("云安全平台".encode() in payload
                    or b"Your current behavior is detected as abnormal" in payload):
                    response.raise_for_status()
                if response.status_code in (403, 404) and url == urls[0]:
                    continue
                response.raise_for_status()
            break
        if not payload.startswith(b"%PDF"):
            raise ValueError(f"BSE announcement is not a PDF: {publication.announcement_id}")
        FileSystem.write_bytes_atomic(path, payload)
        original = AnnouncementFile(**publication.model_dump(exclude={"source", "url"}),
            url=url, fetched_at=fetched_at)
        FileSystem.write_bytes_atomic(receipt, original.model_dump_json(indent=2).encode())
        time.sleep(0.5)
        return original
