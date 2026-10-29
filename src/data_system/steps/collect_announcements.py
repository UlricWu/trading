# filepath: src/data_system/steps/collect_announcements.py
"""Collect bounded public announcement evidence for committed dividend candidates."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow.parquet as pq
from pydantic import TypeAdapter
import requests

from src import logs
from src.access import meta
from src.data_system.announcement_evidence import AnnouncementCollection, SecurityIdentity
from src.data_system.announcement_publications import AnnouncementPublication
from src.data_system.brokers.cninfo import CninfoBroker
from src.data_system.context import DataContext
from src.data_system.distribution_announcements import ReviewExtractionManifest
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class CollectAnnouncementsStep:
    """Publish original PDFs and a completeness manifest, partitioned by registration.

    Example:
        step = CollectAnnouncementsStep(pm=pm, announcement_start=date(2026, 8, 20),
            announcement_end=date(2026, 10, 17), clock=utc_now)
        context = step.run(context)
    """

    pm: PathManager
    announcement_start: date
    announcement_end: date
    clock: Callable[[], datetime]
    identity_refs: tuple[Path, ...] = ()
    observed_publications: tuple[AnnouncementPublication, ...] = ()
    review_refs: tuple[Path, ...] = ()

    def run(self, context: DataContext) -> DataContext:
        """Reuse exact collected inputs or require a complete source query before publication.

        Example:
            context = step.run(DataContext(start="2026-09-01", end="2026-09-30", trade_dates=dates))
        """
        supplied = {}
        originals = {}
        supplied_reviews = tuple(review for ref in self.review_refs
            for review in TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(ref.read_bytes()))
        for ref in self.identity_refs:
            for identity in TypeAdapter(tuple[SecurityIdentity, ...]).validate_json(ref.read_bytes()):
                if identity.published_symbol in supplied and supplied[identity.published_symbol] != identity:
                    raise ValueError(f"conflicting historical security identity: {identity.published_symbol}")
                original = ref.parent / identity.evidence_file
                payload = original.read_bytes()
                supplied[identity.published_symbol] = identity
                originals[identity.evidence_file] = payload
        with requests.Session() as session:
            broker = CninfoBroker(session=session, clock=self.clock)
            organizations = None
            for day in context.trade_dates:
                source = self.pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
                loaded = meta.require(pm=self.pm, meta_path=source.meta_path, expected_payload_path=source.payload_path)
                with pq.ParquetFile(loaded.payload_path) as parquet:
                    rows = TypeAdapter(tuple[DividendRecord, ...]).validate_python(parquet.read().to_pylist())
                all_symbols = {row.symbol for row in rows if row.div_proc == "实施"}
                symbols = {row.symbol for row in rows if row.div_proc == "实施" and not row.ts_code.endswith(".BJ")}
                review_path = self.pm.raw_payload(broker="announcements", source_name="corporate_action_review_inputs",
                    trade_date=day, payload_file="reviews.json")
                reviewed = meta.find(pm=self.pm, meta_path=review_path.parent / "meta.json", expected_payload_path=review_path)
                reviews = (() if reviewed is None else
                    TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(review_path.read_bytes()))
                for review in (*reviews, *supplied_reviews):
                    origin = review.announcement_source
                    if origin is not None and review.record_date.isoformat() == day:
                        all_symbols.add(origin.symbol)
                        if origin.source == "cninfo":
                            symbols.add(origin.symbol)
                if not symbols:
                    continue
                path = self.pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
                    trade_date=day, payload_file="announcements.json")
                committed = meta.find(pm=self.pm, meta_path=path.parent / "meta.json", expected_payload_path=path)
                extraction_path = self.pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
                    trade_date=day, payload_file="reviews.json").parent / "extraction.json"
                retry_symbols = set()
                if extraction_path.exists():
                    issues = ReviewExtractionManifest.model_validate_json(extraction_path.read_bytes()).issues
                    retry_symbols = {candidate.split(".", maxsplit=1)[0] for candidate, reason in issues.items()
                        if reason in {"missing_implementation", "implementation_event_not_matched"}}
                documents = []
                counts = {}
                identities = {code: identity for code, identity in supplied.items() if identity.symbol in symbols}
                if committed is not None:
                    saved = AnnouncementCollection.model_validate_json(path.read_bytes())
                    for identity in saved.security_identities:
                        if identity.published_symbol in identities and identities[identity.published_symbol] != identity:
                            raise ValueError(f"security identity changed: {identity.published_symbol}")
                        if identity.symbol in symbols:
                            identities[identity.published_symbol] = identity
                    known = {row.announcement_id: (row.symbol, row.title, row.published_date, row.published_ts_us, row.url)
                             for row in saved.documents}
                    observation_matches = all(known.get(row.announcement_id) ==
                        (row.symbol, row.title, row.published_date, row.published_ts_us, row.url) for row in self.observed_publications)
                    if (saved.announcement_start == self.announcement_start
                        and saved.announcement_end == self.announcement_end
                        and set(saved.index_counts) <= all_symbols):
                        if (symbols <= saved.index_counts.keys() and set(identities.values()) == set(saved.security_identities)
                            and observation_matches and not symbols.intersection(retry_symbols)):
                            logs.info(f"♻️ announcement collection; record_date={day} documents={len(saved.documents)}")
                            continue
                        if observation_matches:
                            retry_codes = retry_symbols | {row.published_symbol for row in identities.values()
                                if row.symbol in retry_symbols}
                            documents.extend(row for row in saved.documents if row.symbol not in retry_codes)
                            counts.update({symbol: count for symbol, count in saved.index_counts.items()
                                if symbol not in retry_symbols})
                for identity in identities.values():
                    if identity.evidence_file in originals:
                        FileSystem.write_bytes_atomic(path.parent / identity.evidence_file,
                            originals[identity.evidence_file])
                missing_symbols = symbols - counts.keys()
                if missing_symbols and organizations is None:
                    organizations = broker.stock_ids(directory=path.parent)
                for symbol in sorted(missing_symbols):
                    if organizations is None or symbol not in organizations:
                        raise ValueError(f"announcement organization unavailable: {symbol}/{day}")
                    index = broker.company_index(symbol=symbol, org_id=organizations[symbol],
                        start=self.announcement_start, end=self.announcement_end, directory=path.parent,
                        historical_symbols=tuple(row.published_symbol for row in identities.values() if row.symbol == symbol))
                    if not index:
                        raise ValueError(f"announcement index empty for implemented dividend: {symbol}/{day}")
                    counts[symbol] = len(index)
                    documents.extend(broker.download_distribution_documents(index=index, directory=path.parent,
                        implementation_dates=frozenset(row.imp_ann_date for row in rows
                            if row.symbol == symbol and row.imp_ann_date is not None)))
                collection = AnnouncementCollection(version="v1",
                    announcement_start=self.announcement_start, announcement_end=self.announcement_end,
                    collected_at=self.clock(), documents=tuple(documents), index_counts=counts,
                    security_identities=tuple(identities.values()),
                    request_files=tuple(sorted({request.name for request in path.parent.glob("query-*.json")}
                        | {row.evidence_file for row in identities.values()})))
                FileSystem.write_bytes_atomic(path, collection.model_dump_json(indent=2).encode())
                meta.commit(pm=self.pm, payload_path=path)
                logs.info(f"✅ announcement collection; record_date={day} symbols={len(symbols)} documents={len(documents)}")
        return context
