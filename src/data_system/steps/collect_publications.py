# filepath: src/data_system/steps/collect_publications.py
"""Collect complete natural-day indexes independently of the local dividend universe."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
import json

import requests

from src import logs
from src.access import meta
from src.data_system.announcement_publications import PublicationCoverage, PublicationIndex
from src.data_system.brokers.bse import BseBroker
from src.data_system.brokers.cninfo import CninfoBroker
from src.data_system.context import DataContext
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class CollectPublicationsStep:
    """Refresh both public indexes and retain earlier observations when source content changes.

    Example:
        context = CollectPublicationsStep(pm, utc_now).run(DataContext(start="2026-09-19", end="2026-09-19"))
    """

    pm: PathManager
    clock: Callable[[], datetime]

    def run(self, context: DataContext) -> DataContext:
        """Query every natural day, preserving complete pagination evidence before committing.

        Example:
            context = step.run(DataContext(start="2026-09-19", end="2026-09-20"))
        """
        with requests.Session() as cninfo_session, requests.Session() as bse_session:
            brokers = (("cninfo", CninfoBroker(session=cninfo_session, clock=self.clock)),
                       ("bse", BseBroker(session=bse_session, clock=self.clock)))
            start, end = date.fromisoformat(context.start), date.fromisoformat(context.end)
            days = {start + timedelta(days=offset) for offset in range((end - start).days + 1)}
            source_roots = tuple(self.pm.raw_source_dir(broker=provider, source_name="corporate_action_publications")
                for provider, _ in brokers)
            partitions = {path.name for root in source_roots for path in root.glob("trade_date=*")}
            for partition in partitions:
                if any(not (root / partition / "meta.json").exists() or (root / partition / "pending.json").exists()
                    for root in source_roots):
                    days.add(date.fromisoformat(partition.removeprefix("trade_date=")))
            for day in sorted(days):
                for provider, broker in brokers:
                    path = self.pm.raw_payload(broker=provider, source_name="corporate_action_publications",
                        trade_date=day.isoformat(), payload_file="index.json")
                    existing = meta.find(pm=self.pm, meta_path=path.parent / "meta.json", expected_payload_path=path)
                    pending = path.parent / "pending.json"
                    FileSystem.write_bytes_atomic(pending, json.dumps({"started_at": self.clock().isoformat()}).encode())
                    rows = broker.announcement_index(start=day, end=day, directory=path.parent)
                    index = PublicationIndex(version="v1", source=provider, publication_date=day,
                        announcements=tuple(sorted(rows, key=lambda row: row.announcement_id)))
                    payload = index.model_dump_json(indent=2).encode()
                    coverage_path = path.parent / "coverage.json"
                    coverage = PublicationCoverage(index_sha256=sha256(payload).hexdigest(), index_count=len(rows),
                        observed_at=self.clock(), request_sha256={item.name: sha256(item.read_bytes()).hexdigest()
                            for item in sorted(path.parent.glob("query-*.json"))})
                    if existing is not None and path.read_bytes() != payload:
                        originals = {item.name: item.read_bytes() for item in
                            (path, path.parent / "meta.json", coverage_path) if item.exists()}
                        archive_id = sha256(b"".join(originals[name] for name in sorted(originals))).hexdigest()
                        for name, original in originals.items():
                            FileSystem.write_bytes_atomic(path.parent / "history" / archive_id / name, original)
                    if existing is None or path.read_bytes() != payload:
                        FileSystem.write_bytes_atomic(path, payload)
                        FileSystem.write_bytes_atomic(coverage_path, coverage.model_dump_json(indent=2).encode())
                        meta.commit(pm=self.pm, payload_path=path)
                    else:
                        FileSystem.write_bytes_atomic(coverage_path, coverage.model_dump_json(indent=2).encode())
                    FileSystem.remove(pending)
                    logs.info(f"✅ publication index; source={provider} day={day} entries={len(rows)}")
        return context
