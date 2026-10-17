# filepath: src/data_system/steps/collect_announcements.py
"""Collect bounded public announcement evidence for committed dividend candidates."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256

import pyarrow.parquet as pq
from pydantic import TypeAdapter
import requests

from src import logs
from src.access import meta
from src.data_system.brokers.cninfo import AnnouncementCollection, CninfoBroker
from src.data_system.context import DataContext
from src.data_system.corporate_actions import DividendRecord
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

    def run(self, context: DataContext) -> DataContext:
        """Reuse exact collected inputs or require a complete source query before publication.

        Example:
            context = step.run(DataContext(start="2026-09-01", end="2026-09-30", trade_dates=dates))
        """
        with requests.Session() as session:
            broker = CninfoBroker(session=session, clock=self.clock)
            organizations = None
            for day in context.trade_dates:
                source = self.pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
                loaded = meta.require(pm=self.pm, meta_path=source.meta_path, expected_payload_path=source.payload_path)
                digest = sha256(loaded.payload_path.read_bytes()).hexdigest()
                path = self.pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
                    trade_date=day, payload_file="announcements.json")
                committed = meta.find(pm=self.pm, meta_path=path.parent / "meta.json", expected_payload_path=path)
                if committed is not None:
                    saved = AnnouncementCollection.model_validate_json(path.read_bytes())
                    if (saved.dividend_sha256 == digest and saved.announcement_start == self.announcement_start
                        and saved.announcement_end == self.announcement_end):
                        for document in saved.documents:
                            original = path.parent / f"{document.announcement_id}.pdf"
                            if sha256(original.read_bytes()).hexdigest() != document.sha256:
                                raise ValueError(f"announcement PDF digest changed: {original}")
                        for name, expected in saved.request_sha256.items():
                            if sha256((path.parent / name).read_bytes()).hexdigest() != expected:
                                raise ValueError(f"announcement request evidence changed: {name}")
                        logs.info(f"♻️ announcement collection; record_date={day} documents={len(saved.documents)}")
                        continue
                with pq.ParquetFile(loaded.payload_path) as parquet:
                    rows = TypeAdapter(tuple[DividendRecord, ...]).validate_python(parquet.read().to_pylist())
                symbols = sorted({row.symbol for row in rows if row.div_proc == "实施" and row.ts_code.endswith((".SH", ".SZ"))})
                if symbols and organizations is None:
                    organizations = broker.stock_ids(directory=path.parent)
                documents = []
                counts = {}
                for symbol in symbols:
                    if organizations is None or symbol not in organizations:
                        raise ValueError(f"announcement organization unavailable: {symbol}/{day}")
                    index = broker.company_index(symbol=symbol, org_id=organizations[symbol],
                        start=self.announcement_start, end=self.announcement_end, directory=path.parent)
                    if not index:
                        raise ValueError(f"announcement index empty for implemented dividend: {symbol}/{day}")
                    counts[symbol] = len(index)
                    documents.extend(broker.download_distribution_documents(index=index, directory=path.parent))
                collection = AnnouncementCollection(version="v1", dividend_sha256=digest,
                    announcement_start=self.announcement_start, announcement_end=self.announcement_end,
                    collected_at=self.clock(), documents=tuple(documents), index_counts=counts,
                    request_sha256={request.name: sha256(request.read_bytes()).hexdigest()
                                    for request in sorted(path.parent.glob("query-*.json"))})
                FileSystem.write_bytes_atomic(path, collection.model_dump_json(indent=2).encode())
                meta.commit(pm=self.pm, payload_path=path)
                logs.info(f"✅ announcement collection; record_date={day} symbols={len(symbols)} documents={len(documents)}")
        return context
