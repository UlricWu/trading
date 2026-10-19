# filepath: tests/fixtures/announcement_evidence.py
"""Exercise evidence publication, source binding and replay-visible dispositions."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pyarrow as pa
from pypdf import PdfWriter
import pytest

from src.access import meta
from src.data_system.announcement_evidence import AnnouncementCollection, AnnouncementFile
from src.data_system.context import DataContext
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


@pytest.fixture
def evidence(tmp_path: Path) -> tuple[PathManager, DataContext, Path, Path]:
    case = json.loads((Path(__file__).parent / "corporate_actions/verified_distributions.json").read_text())[1]
    day = case["review"]["record_date"]
    pm = PathManager(tmp_path)
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements", trade_date=day,
                          payload_file="announcements.json")
    path.parent.mkdir(parents=True)
    pdf = path.parent / "1.pdf"
    with PdfWriter() as writer:
        writer.add_blank_page(width=100, height=100)
        writer.write(pdf)
    document = AnnouncementFile(announcement_id="1", symbol=case["records"][0]["symbol"],
        title="fixture", published_date=case["review"]["terms"][0]["published_date"],
        url="https://static.cninfo.com.cn/finalpage/2024-07-18/1.PDF",
        sha256=sha256(pdf.read_bytes()).hexdigest(), fetched_at=datetime(2026, 10, 17, tzinfo=timezone.utc))
    collection = AnnouncementCollection(version="v1", dividend_sha256=sha256(source.payload_path.read_bytes()).hexdigest(),
        announcement_start="2024-01-01", announcement_end="2026-10-17",
        collected_at=datetime(2026, 10, 17, tzinfo=timezone.utc), documents=(document,),
        index_counts={document.symbol: 1}, request_sha256={})
    FileSystem.write_bytes_atomic(path, collection.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    case["review"]["evidence_refs"] = [document.url + "#sha256=" + document.sha256]
    supplied = tmp_path / "review-input.json"
    supplied.write_text(json.dumps([case["review"]]))
    return pm, DataContext(start=day, end=day, trade_dates=(day,)), supplied, pdf
