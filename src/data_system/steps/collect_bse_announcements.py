# filepath: src/data_system/steps/collect_bse_announcements.py
"""Collect BSE candidate evidence using verified current and historical source codes."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import pyarrow.parquet as pq
from pydantic import TypeAdapter
import requests

from src import logs
from src.access import meta
from src.data_system.announcement_evidence import AnnouncementCollection, preserve_announcement_collection
from src.data_system.announcement_publications import AnnouncementPublication
from src.data_system.brokers.bse import BseBroker
from src.data_system.context import DataContext
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class CollectBseAnnouncementsStep:
    """Publish complete official BSE indexes without changing original PDF security codes.

    Example:
        context = CollectBseAnnouncementsStep(pm, date(2019, 4, 1),
            date(2019, 6, 30), utc_now).run(context)
    """

    pm: PathManager
    announcement_start: date
    announcement_end: date
    clock: Callable[[], datetime]
    observed_publications: tuple[AnnouncementPublication, ...] = ()
    review_refs: tuple[Path, ...] = ()

    def run(self, context: DataContext) -> DataContext:
        """Resolve lookup identities, collect both codes and retain the primary mapping evidence.

        Example:
            context = step.run(DataContext(start="2019-05-21", end="2019-05-21", trade_dates=("2019-05-21",)))
        """
        supplied_reviews = tuple(review for ref in self.review_refs
            for review in TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(ref.read_bytes()))
        with requests.Session() as session:
            broker = BseBroker(session=session, clock=self.clock)
            identities = None
            identity_directory: Path | None = None
            for day in context.trade_dates:
                source = self.pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
                loaded = meta.require(pm=self.pm, meta_path=source.meta_path, expected_payload_path=source.payload_path)
                digest = sha256(loaded.payload_path.read_bytes()).hexdigest()
                with pq.ParquetFile(loaded.payload_path) as parquet:
                    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(parquet.read().to_pylist())
                symbols = {row.symbol for row in records if row.div_proc == "实施" and row.ts_code.endswith(".BJ")}
                review_path = self.pm.raw_payload(broker="announcements", source_name="corporate_action_review_inputs",
                    trade_date=day, payload_file="reviews.json")
                reviewed = meta.find(pm=self.pm, meta_path=review_path.parent / "meta.json", expected_payload_path=review_path)
                reviews = (() if reviewed is None else
                    TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(review_path.read_bytes()))
                symbols.update(review.announcement_source.symbol for review in (*reviews, *supplied_reviews)
                    if review.announcement_source is not None and review.announcement_source.source == "bse"
                    and review.record_date.isoformat() == day)
                if not symbols:
                    continue
                path = self.pm.raw_payload(broker="bse", source_name="corporate_action_announcements",
                    trade_date=day, payload_file="announcements.json")
                committed = meta.find(pm=self.pm, meta_path=path.parent / "meta.json", expected_payload_path=path)
                if committed is not None:
                    saved = AnnouncementCollection.model_validate_json(path.read_bytes())
                    for document in saved.documents:
                        if sha256((path.parent / f"{document.announcement_id}.pdf").read_bytes()).hexdigest() != document.sha256:
                            raise ValueError(f"BSE announcement PDF digest changed: {document.announcement_id}")
                    for name, expected in saved.request_sha256.items():
                        if sha256((path.parent / name).read_bytes()).hexdigest() != expected:
                            raise ValueError(f"BSE announcement request evidence changed: {name}")
                    known = {row.announcement_id: (row.symbol, row.title, row.published_date, row.published_ts_us, urlsplit(row.url).path)
                             for row in saved.documents}
                    observation_matches = all(known.get(row.announcement_id) ==
                        (row.symbol, row.title, row.published_date, row.published_ts_us, urlsplit(row.url).path)
                        for row in self.observed_publications)
                    if (saved.dividend_sha256 == digest and saved.announcement_start == self.announcement_start
                        and saved.announcement_end == self.announcement_end and set(saved.index_counts) == symbols
                        and saved.query_scope == {"disclosureType": "5", "isNewThree": "", "xxfcbj": ""}
                        and observation_matches):
                        logs.info(f"♻️ BSE announcement collection; record_date={day}")
                        continue
                    preserve_announcement_collection(path=path, collection=saved)
                if identities is None:
                    identities = broker.security_identities(directory=path.parent)
                    identity_directory = path.parent
                elif identity_directory != path.parent:
                    for name in ("security-code-mapping.html", "security-code-mapping.request.json"):
                        FileSystem.write_bytes_atomic(path.parent / name, (identity_directory / name).read_bytes())
                aliases = tuple(row for row in identities if row.symbol in symbols)
                documents = []
                counts = {}
                for symbol in sorted(symbols):
                    codes = {symbol, *(row.published_symbol for row in aliases if row.symbol == symbol)}
                    index = {}
                    for code in sorted(codes):
                        rows = broker.company_index(symbol=code, start=self.announcement_start,
                            end=self.announcement_end, directory=path.parent)
                        for row in rows:
                            if row.announcement_id in index and index[row.announcement_id] != row:
                                raise ValueError(f"BSE announcement identity changed between code queries: {row.announcement_id}")
                            index[row.announcement_id] = row
                    if not index:
                        raise ValueError(f"BSE announcement index empty for implemented dividend: {symbol}/{day}")
                    counts[symbol] = len(index)
                    documents.extend(broker.download_distribution_documents(index=tuple(index.values()), directory=path.parent))
                evidence = (*path.parent.glob("query-*.json"), *path.parent.glob("download-attempt-*"),
                            path.parent / "security-code-mapping.html",
                            path.parent / "security-code-mapping.request.json")
                collection = AnnouncementCollection(version="v1", dividend_sha256=digest,
                    announcement_start=self.announcement_start, announcement_end=self.announcement_end,
                    collected_at=self.clock(), documents=tuple(documents), index_counts=counts,
                    security_identities=aliases,
                    query_scope={"disclosureType": "5", "isNewThree": "", "xxfcbj": ""},
                    request_sha256={item.name: sha256(item.read_bytes()).hexdigest() for item in evidence})
                FileSystem.write_bytes_atomic(path, collection.model_dump_json(indent=2).encode())
                meta.commit(pm=self.pm, payload_path=path)
                logs.info(f"✅ BSE announcement collection; record_date={day} symbols={len(symbols)} documents={len(documents)}")
        return context
