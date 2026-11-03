# filepath: src/data_system/steps/materialize_announcement_facts.py
"""Publish natural-day announcement facts without requiring dividend candidates."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json

import pyarrow as pa
import pyarrow.parquet as pq
from pypdf import PdfReader
from pypdf.errors import PyPdfError
import requests

from src.access import meta
from src.data_system.announcement_facts import AnnouncementFacts, extract_announcement_facts
from src.data_system.announcement_publications import PublicationIndex, is_distribution_evidence
from src.data_system.brokers.bse import BseBroker
from src.data_system.brokers.cninfo import CninfoBroker
from src.data_system.context import DataContext
from src.data_system.distribution_announcements import AnnouncementText
from src.data_system.steps._partition import _publish_parquet_object
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class MaterializeAnnouncementFactsStep:
    """Keep original PDFs, text and structured partial facts in disclosure order.

    Example:
        step = MaterializeAnnouncementFactsStep(pm=pm, clock=utc_now)
        step.run(DataContext(start="2026-09-19", end="2026-09-20"))
    """

    pm: PathManager
    clock: Callable[[], datetime]

    def run(self, context: DataContext) -> DataContext:
        """Build requested and previously unmaterialized publication partitions.

        Example:
            context = step.run(context)
        """
        indexes = {}
        for provider in ("cninfo", "bse"):
            root = self.pm.raw_source_dir(broker=provider, source_name="corporate_action_publications")
            for path in root.glob("trade_date=*/index.json"):
                day = path.parent.name.removeprefix("trade_date=")
                if day <= context.end:
                    meta.require(pm=self.pm, meta_path=path.parent / "meta.json", expected_payload_path=path)
                    indexes.setdefault(day, []).append(path)
        previous = []
        with requests.Session() as cninfo_session, requests.Session() as bse_session:
            brokers = {"cninfo": CninfoBroker(session=cninfo_session, clock=self.clock),
                       "bse": BseBroker(session=bse_session, clock=self.clock)}
            for day, paths in sorted(indexes.items()):
                output = self.pm.processed_object(dataset_name="announcement_facts", version="v1", trade_date=day)
                existing = meta.find(pm=self.pm, meta_path=output.meta_path, expected_payload_path=output.payload_path)
                saved = ()
                if existing is not None:
                    with pq.ParquetFile(existing.payload_path) as parquet:
                        saved = tuple(AnnouncementFacts.model_validate_json(value) for value in parquet.read()["facts"].to_pylist())
                if existing is not None and day < context.start and day not in context.publication_dates:
                    previous.extend(saved)
                    continue
                rows = []
                seen_at = {(row.publication.source, row.publication.announcement_id): row.observed_at for row in saved}
                publications = [(path, publication) for path in paths
                    for publication in PublicationIndex.model_validate_json(path.read_bytes()).announcements
                    if is_distribution_evidence(publication.title)]
                for path, publication in sorted(publications, key=lambda item: (
                    item[1].available_ts_us, item[1].source, item[1].announcement_id,
                )):
                    directory = path.parent / "documents" / publication.announcement_id
                    pdf = directory / "original.pdf"
                    brokers[publication.source].download_announcement(publication=publication, path=pdf)
                    text_path = directory / "text.json"
                    issue = None
                    if text_path.exists():
                        document = AnnouncementText.model_validate_json(text_path.read_bytes())
                    else:
                        try:
                            with pdf.open("rb") as stream:
                                pages = tuple(page.extract_text() or "" for page in PdfReader(stream).pages)
                        except (PyPdfError, UnicodeError) as exc:
                            pages = ()
                            issue = f"pdf_text_unreadable:{type(exc).__name__}"
                        if not any(page.strip() for page in pages):
                            issue = issue or "pdf_text_unreadable:empty_text"
                        document = AnnouncementText(**publication.model_dump(), pages=pages)
                        if issue is None:
                            FileSystem.write_bytes_atomic(text_path, document.model_dump_json(indent=2).encode())
                    facts = extract_announcement_facts(document, observed_at=seen_at.get(
                        (publication.source, publication.announcement_id), self.clock()), previous=previous)
                    if issue is not None:
                        facts = facts.model_copy(update={"parse_issue": issue})
                    rows.append(facts)
                    previous.append(facts)
                schema = pa.schema([("event_id", pa.string()), ("symbol", pa.string()),
                    ("published_date", pa.date32()), ("available_ts_us", pa.int64()), ("observed_at", pa.timestamp("us", tz="UTC")),
                    ("kind", pa.string()), ("benefit_state", pa.string()),
                    *((field, pa.date32()) for field in ("record_date", "ex_date", "pay_date", "shares_credit_date", "shares_available_date")),
                    *((field, pa.string()) for field in ("cash_per_share", "shares_per_share", "shares_numerator", "shares_denominator")),
                    ("facts", pa.string())], metadata={b"announcement_inputs": json.dumps(
                        [str(path.relative_to(self.pm.storage_root)) for path in sorted(paths)]).encode()})
                table = pa.Table.from_pylist([{
                    "event_id": row.event_id, "symbol": row.publication.symbol, "published_date": row.publication.published_date,
                    "available_ts_us": row.available_ts_us, "observed_at": row.observed_at, "kind": row.kind,
                    "benefit_state": row.benefit_state,
                    **{field: getattr(row, field) for field in ("record_date", "ex_date", "pay_date", "shares_credit_date", "shares_available_date")},
                    **{field: str(value) if (value := getattr(row, field)) is not None else None
                        for field in ("cash_per_share", "shares_per_share", "shares_numerator", "shares_denominator")},
                    "facts": row.model_dump_json(),
                } for row in rows], schema=schema)
                if existing is not None:
                    with pq.ParquetFile(existing.payload_path) as parquet:
                        if parquet.read().equals(table, check_metadata=True):
                            continue
                _publish_parquet_object(pm=self.pm, paths=output, table=table)
        return context
