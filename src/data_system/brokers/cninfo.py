# filepath: src/data_system/brokers/cninfo.py
"""Read public CNINFO announcement indexes and preserve their original PDFs."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from uuid import uuid4
import re
import time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
import requests

from src.data_system.announcement_evidence import AnnouncementFile
from src.data_system.announcement_publications import AnnouncementPublication, is_distribution_evidence
from src.utils.filesystem import FileSystem


class _IndexRow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    announcement_id: str = Field(alias="announcementId", pattern=r"^[0-9]+$")
    symbol: str = Field(alias="secCode", pattern=r"^[0-9]{6}$")
    org_id: str = Field(alias="orgId", min_length=1)
    title: str = Field(alias="announcementTitle")
    timestamp_ms: int = Field(alias="announcementTime")
    adjunct_url: str = Field(alias="adjunctUrl", pattern=r"^finalpage/\d{4}-\d{2}-\d{2}/[0-9]+\.[Pp][Dd][Ff]$")


class _IndexPage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    total: int = Field(alias="totalAnnouncement", ge=0)
    announcements: tuple[_IndexRow, ...] | None


class _StockRow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    code: str
    org_id: str = Field(alias="orgId")


class _StockList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    stocks: tuple[_StockRow, ...] = Field(alias="stockList")


class CninfoBroker:
    """Fetch bounded public sources using a caller-owned HTTP session.

    Example:
        with requests.Session() as session:
            broker = CninfoBroker(session=session, clock=utc_now)
            organizations = broker.stock_ids(directory=raw_directory)
            index = broker.company_index(symbol="688808", org_id=organizations["688808"],
                start=date(2026, 8, 20), end=date(2026, 10, 17), directory=raw_directory)
    """

    def __init__(self, *, session: requests.Session, clock: Callable[[], datetime]) -> None:
        """Bind transport and collection time without reading sources at construction.

        Example:
            broker = CninfoBroker(session=session, clock=utc_now)
        """
        self._session = session
        self._clock = clock
        self._session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"})

    def stock_ids(self, *, directory: Path) -> dict[str, str]:
        """Read source lookup identifiers; these never define a historical stock universe.

        Example:
            organizations = broker.stock_ids(directory=raw_directory)
        """
        with self._session.get("https://www.cninfo.com.cn/new/data/szse_stock.json", timeout=30) as response:
            response.raise_for_status()
            payload = response.content
        parsed = _StockList.model_validate_json(payload)
        FileSystem.write_bytes_atomic(directory / "stock-lookup.json", payload)
        return {row.code: row.org_id for row in parsed.stocks}

    def company_index(
        self, *, symbol: str, org_id: str, start: date, end: date, directory: Path,
        historical_symbols: Sequence[str] = (),
    ) -> tuple[_IndexRow, ...]:
        """Require complete unique results, splitting date ranges when pages overlap.

        Example:
            rows = broker.company_index(symbol="688808", org_id="gssh0688808",
                start=date(2026, 9, 1), end=date(2026, 9, 30), directory=raw_directory)
        """
        rows = self._index(parameters={"stock": f"{symbol},{org_id}", "searchkey": ""},
                           start=start, end=end, directory=directory)
        for row in rows:
            if row.symbol not in {symbol, *historical_symbols}:
                raise ValueError(f"announcement index outside query: {row.announcement_id}")
        return rows

    def distribution_index(self, *, start: date, end: date, directory: Path) -> tuple[_IndexRow, ...]:
        """Read the public distribution category independently of existing dividend candidates.

        This proves only this category's bounded index coverage; historical NEEQ and
        uncategorized notices need separate source coverage.

        Example:
            rows = broker.distribution_index(start=date(2026, 9, 1), end=date(2026, 9, 30),
                                             directory=raw_directory)
        """
        return self._index(parameters={"stock": "", "searchkey": "", "plate": "",
            "category": "category_qyfpxzcs_szsh"}, start=start, end=end, directory=directory)

    def announcement_index(self, *, start: date, end: date, directory: Path) -> tuple[AnnouncementPublication, ...]:
        """Discover all indexed publications, including corrections outside the dividend category.

        Example:
            rows = broker.announcement_index(start=date(2026, 9, 19), end=date(2026, 9, 19), directory=raw_directory)
        """
        rows = self._index(parameters={"stock": "", "searchkey": "", "plate": "", "category": ""},
            start=start, end=end, directory=directory)
        publications = []
        for row in rows:
            published = datetime.fromtimestamp(row.timestamp_ms / 1000, ZoneInfo("Asia/Shanghai"))
            publications.append(AnnouncementPublication(source="cninfo", announcement_id=row.announcement_id,
                symbol=row.symbol, title=re.sub(r"<[^>]+>", "", row.title), published_date=published.date(),
                published_ts_us=row.timestamp_ms * 1000 if published.time() != datetime.min.time() else None,
                url=f"https://static.cninfo.com.cn/{row.adjunct_url}"))
        return tuple(publications)

    def _index(
        self, *, parameters: Mapping[str, str], start: date, end: date, directory: Path,
    ) -> tuple[_IndexRow, ...]:
        observed = self._observe_index(parameters=parameters, start=start, end=end, directory=directory)
        rows = observed.announcements or ()
        if len(rows) != observed.total:
            scope = parameters.get("stock") or parameters.get("category")
            raise ValueError(f"announcement index incomplete: {scope}/{start}/{end} expected={observed.total} actual={len(rows)}")
        return rows

    def _observe_index(
        self, *, parameters: Mapping[str, str], start: date, end: date, directory: Path,
    ) -> _IndexPage:
        scope = parameters.get("stock") or parameters.get("category")

        def request(page: int, order: str) -> _IndexPage:
            query = {**parameters, "pageNum": page, "pageSize": 30, "column": "szse", "tabName": "fulltext",
                "seDate": f"{start}~{end}",
                "sortName": "time", "sortType": order, "isHLtitle": "false"}
            for attempt in range(3):
                with self._session.post("https://www.cninfo.com.cn/new/hisAnnouncement/query",
                                        data=query, timeout=30) as response:
                    payload = response.content
                    collected_at = self._clock().isoformat()
                    identity = uuid4().hex
                    FileSystem.write_bytes_atomic(directory / f"query-{identity}.json", payload)
                    FileSystem.write_bytes_atomic(directory / f"query-{identity}.request.json", json.dumps({
                        "url": "https://www.cninfo.com.cn/new/hisAnnouncement/query", "parameters": query,
                        "collected_at": collected_at, "http_status": response.status_code,
                    }, ensure_ascii=False, sort_keys=True).encode())
                    if response.status_code in (500, 502, 503, 504) and attempt < 2:
                        time.sleep(attempt + 1)
                        continue
                    response.raise_for_status()
                break
            parsed = _IndexPage.model_validate_json(payload)
            time.sleep(0.3)
            return parsed

        first = request(1, "desc")
        unique: dict[str, _IndexRow] = {}

        def merge(rows: Sequence[_IndexRow]) -> None:
            for row in rows:
                if row.announcement_id in unique and unique[row.announcement_id] != row:
                    raise ValueError(f"announcement identity changed during collection: {row.announcement_id}")
                unique[row.announcement_id] = row

        merge(first.announcements or ())
        for page in range(2, (first.total + 29) // 30 + 1):
            following = request(page, "desc")
            if following.total != first.total:
                raise ValueError(f"announcement index changed during collection: {scope}/{start}/{end}")
            merge(following.announcements or ())
        if len(unique) != first.total:
            if start < end:
                middle = start + (end - start) // 2
                for lower, upper in ((start, middle), (middle + timedelta(days=1), end)):
                    observed = self._observe_index(parameters=parameters, start=lower, end=upper, directory=directory)
                    merge(observed.announcements or ())
            else:
                for page in range(1, (first.total + 29) // 30 + 1):
                    following = request(page, "asc")
                    if following.total != first.total:
                        raise ValueError(f"announcement index changed during collection: {scope}/{start}")
                    merge(following.announcements or ())
        if len(unique) != first.total:
            if start == end and not parameters.get("stock"):
                boards = {"": ("sz", "sh", "bj"), "sz": ("szmb", "szcy"), "sh": ("shmb", "shkcp")}
                for board in boards.get(parameters.get("plate", ""), ()):
                    observed = self._observe_index(parameters={**parameters, "plate": board},
                        start=start, end=end, directory=directory)
                    merge(observed.announcements or ())
                if len(unique) != first.total:
                    issuers = sorted({(row.symbol, row.org_id) for row in unique.values()})
                    for symbol, org_id in issuers:
                        observed = self._observe_index(parameters={**parameters, "stock": f"{symbol},{org_id}"},
                            start=start, end=end, directory=directory)
                        merge(observed.announcements or ())
        for row in unique.values():
            public_date = datetime.fromtimestamp(row.timestamp_ms / 1000, ZoneInfo("Asia/Shanghai")).date()
            if not start <= public_date <= end:
                raise ValueError(f"announcement index outside query: {row.announcement_id}")
        return _IndexPage(totalAnnouncement=first.total,
            announcements=tuple(sorted(unique.values(), key=lambda row: (row.timestamp_ms, row.announcement_id))))

    def download_distribution_documents(
        self, *, index: Sequence[_IndexRow], directory: Path, implementation_dates: Collection[date] = (),
    ) -> tuple[AnnouncementFile, ...]:
        """Preserve distribution PDFs and generic corrections; unrelated price adjustments are excluded.

        Example:
            documents = broker.download_distribution_documents(index=index, directory=raw_directory,
                implementation_dates=(date(2026, 9, 1),))
        """
        documents = []
        for row in index:
            title = re.sub(r"<[^>]+>", "", row.title)
            published = datetime.fromtimestamp(row.timestamp_ms / 1000, ZoneInfo("Asia/Shanghai"))
            if not is_distribution_evidence(title) and published.date() not in implementation_dates:
                continue
            publication = AnnouncementPublication(source="cninfo", announcement_id=row.announcement_id, symbol=row.symbol,
                title=title, published_date=published.date(),
                published_ts_us=row.timestamp_ms * 1000 if published.time() != datetime.min.time() else None,
                url=f"https://static.cninfo.com.cn/{row.adjunct_url}")
            documents.append(self.download_announcement(publication=publication,
                path=directory / f"{row.announcement_id}.pdf"))
        return tuple(documents)

    def download_announcement(self, *, publication: AnnouncementPublication, path: Path) -> AnnouncementFile:
        """Preserve a native PDF and its original download receipt for either data projection.

        Example:
            original = broker.download_announcement(publication=publication, path=pdf_path)
        """
        receipt = path.with_suffix(".download.json")
        if path.exists():
            saved = AnnouncementFile.model_validate_json(receipt.read_bytes())
            if saved.url != publication.url:
                raise ValueError(f"cached announcement identity changed: {publication.announcement_id}")
            return saved
        with self._session.get(publication.url, timeout=40) as response:
            response.raise_for_status()
            payload = response.content
        if not payload.startswith(b"%PDF"):
            raise ValueError(f"announcement is not a PDF: {publication.announcement_id}")
        FileSystem.write_bytes_atomic(path, payload)
        original = AnnouncementFile(**publication.model_dump(exclude={"source"}), fetched_at=self._clock())
        FileSystem.write_bytes_atomic(receipt, original.model_dump_json(indent=2).encode())
        time.sleep(0.2)
        return original
