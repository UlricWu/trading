# filepath: src/data_system/brokers/cninfo.py
"""Read public CNINFO announcement indexes and preserve their original PDFs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Annotated, Self
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
import requests

from src.utils.filesystem import FileSystem


class AnnouncementFile(BaseModel):
    """An original PDF bound to the source's publication date and content digest.

    Example:
        original = AnnouncementFile.model_validate(manifest_row)
        payload = directory / f"{original.announcement_id}.pdf"
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    announcement_id: str = Field(pattern=r"^[0-9]+$")
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    title: str
    published_date: date
    url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fetched_at: AwareDatetime


class AnnouncementCollection(BaseModel):
    """One registration partition's bounded collection and exact source identity.

    Example:
        collection = AnnouncementCollection.model_validate_json(payload.read_bytes())
        print(len(collection.documents))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(pattern=r"^v1$")
    dividend_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    announcement_start: date
    announcement_end: date
    collected_at: AwareDatetime
    documents: tuple[AnnouncementFile, ...]
    index_counts: dict[str, Annotated[int, Field(gt=0)]]
    request_sha256: dict[str, str]

    @model_validator(mode="after")
    def _validate_collection(self) -> Self:
        if self.announcement_start > self.announcement_end:
            raise ValueError("announcement window must be increasing")
        identities = [document.announcement_id for document in self.documents]
        if len(set(identities)) != len(identities):
            raise ValueError("announcement collection must have unique PDF identities")
        if any(document.symbol not in self.index_counts for document in self.documents):
            raise ValueError("announcement PDF lacks company index coverage")
        for name, digest in self.request_sha256.items():
            if Path(name).name != name or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("invalid announcement request evidence identity")
        return self


class _IndexRow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    announcement_id: str = Field(alias="announcementId", pattern=r"^[0-9]+$")
    symbol: str = Field(alias="secCode", pattern=r"^[0-9]{6}$")
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
    ) -> tuple[_IndexRow, ...]:
        """Require complete unique results, splitting date ranges when pages overlap.

        Example:
            rows = broker.company_index(symbol="688808", org_id="gssh0688808",
                start=date(2026, 9, 1), end=date(2026, 9, 30), directory=raw_directory)
        """
        def request(page: int, order: str) -> _IndexPage:
            parameters = {"pageNum": page, "pageSize": 30, "column": "szse", "tabName": "fulltext",
                "stock": f"{symbol},{org_id}", "searchkey": "", "seDate": f"{start}~{end}",
                "sortName": "time", "sortType": order, "isHLtitle": "false"}
            with self._session.post("https://www.cninfo.com.cn/new/hisAnnouncement/query",
                                    data=parameters, timeout=30) as response:
                response.raise_for_status()
                payload = response.content
            parsed = _IndexPage.model_validate_json(payload)
            collected_at = self._clock().isoformat()
            identity = sha256(json.dumps(parameters, sort_keys=True).encode() + payload + collected_at.encode()).hexdigest()
            FileSystem.write_bytes_atomic(directory / f"query-{identity}.json", payload)
            FileSystem.write_bytes_atomic(directory / f"query-{identity}.request.json", json.dumps({
                "url": "https://www.cninfo.com.cn/new/hisAnnouncement/query", "parameters": parameters,
                "collected_at": collected_at, "response_sha256": sha256(payload).hexdigest(),
            }, ensure_ascii=False, sort_keys=True).encode())
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
                raise ValueError(f"announcement index changed during collection: {symbol}/{start}/{end}")
            merge(following.announcements or ())
        if len(unique) != first.total:
            if start < end:
                middle = start + (end - start) // 2
                split = self.company_index(symbol=symbol, org_id=org_id, start=start, end=middle, directory=directory)
                split += self.company_index(symbol=symbol, org_id=org_id, start=middle + timedelta(days=1), end=end, directory=directory)
                merge(split)
            else:
                for page in range(1, (first.total + 29) // 30 + 1):
                    following = request(page, "asc")
                    if following.total != first.total:
                        raise ValueError(f"announcement index changed during collection: {symbol}/{start}")
                    merge(following.announcements or ())
        if len(unique) != first.total:
            raise ValueError(f"announcement index incomplete: {symbol}/{start}/{end} expected={first.total} actual={len(unique)}")
        for row in unique.values():
            public_date = datetime.fromtimestamp(row.timestamp_ms / 1000, ZoneInfo("Asia/Shanghai")).date()
            if row.symbol != symbol or not start <= public_date <= end:
                raise ValueError(f"announcement index outside query: {row.announcement_id}")
        return tuple(sorted(unique.values(), key=lambda row: (row.timestamp_ms, row.announcement_id)))

    def download_distribution_documents(
        self, *, index: Sequence[_IndexRow], directory: Path,
    ) -> tuple[AnnouncementFile, ...]:
        """Preserve distribution PDFs and generic corrections; unrelated price adjustments are excluded.

        Example:
            documents = broker.download_distribution_documents(index=index, directory=raw_directory)
        """
        documents = []
        for row in index:
            title = re.sub(r"<[^>]+>", "", row.title)
            if not re.search(r"分派|分配|分红|派息|转增|除权|除息|更正|补充|修订|取消", title):
                continue
            if re.search(r"(?:回购(?:股份)?|转股|发行|授予|行权)价(?:格)?|律师|独立财务|激励计划|募集资金", title):
                continue
            url = f"https://static.cninfo.com.cn/{row.adjunct_url}"
            path = directory / f"{row.announcement_id}.pdf"
            receipt = path.with_suffix(".download.json")
            if path.exists():
                payload = path.read_bytes()
                saved = AnnouncementFile.model_validate_json(receipt.read_bytes())
                if saved.url != url or saved.sha256 != sha256(payload).hexdigest():
                    raise ValueError(f"cached announcement identity changed: {row.announcement_id}")
                fetched_at = saved.fetched_at
            else:
                with self._session.get(url, timeout=40) as response:
                    response.raise_for_status()
                    payload = response.content
                if not payload.startswith(b"%PDF"):
                    raise ValueError(f"announcement is not a PDF: {row.announcement_id}")
                FileSystem.write_bytes_atomic(path, payload)
                fetched_at = self._clock()
                time.sleep(0.2)
            original = AnnouncementFile(announcement_id=row.announcement_id, symbol=row.symbol,
                title=title, published_date=datetime.fromtimestamp(row.timestamp_ms / 1000, ZoneInfo("Asia/Shanghai")).date(),
                url=url, sha256=sha256(payload).hexdigest(), fetched_at=fetched_at)
            FileSystem.write_bytes_atomic(receipt, original.model_dump_json(indent=2).encode())
            documents.append(original)
        return tuple(documents)
