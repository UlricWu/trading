# filepath: tests/data_system/steps/test_collect_announcements.py
"""Exercise evidence publication, source binding and replay-visible dispositions."""

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import meta
from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.context import DataContext
from src.data_system.steps.collect_announcements import CollectAnnouncementsStep
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager
from tests.fixtures.announcement_evidence import evidence


def test_cninfo_collection_leaves_beijing_candidates_to_the_official_bse_source(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, _, _ = evidence
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    rows = pq.ParquetFile(source.payload_path).read().to_pylist()
    rows.append({**rows[0], "symbol": "920879", "ts_code": "920879.BJ"})
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements", trade_date=context.start,
        payload_file="announcements.json")
    previous = AnnouncementCollection.model_validate_json(path.read_bytes())
    FileSystem.write_bytes_atomic(path, previous.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    broker = Mock()
    broker.stock_ids.return_value = {"920879": "gfbj0830879"}
    broker.company_index.return_value = (object(),)
    broker.download_distribution_documents.return_value = ()
    monkeypatch.setattr("src.data_system.steps.collect_announcements.CninfoBroker", Mock(return_value=broker))
    step = CollectAnnouncementsStep(pm, previous.announcement_start, previous.announcement_end,
        lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    step.run(context)
    saved = AnnouncementCollection.model_validate_json(path.read_bytes())
    assert saved.index_counts == previous.index_counts
    assert saved.documents == previous.documents
    broker.company_index.assert_not_called()
    step.run(context)
    broker.company_index.assert_not_called()


def test_explicit_code_identity_preserves_the_original_proof_and_security_code(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, _, _ = evidence
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    with pq.ParquetFile(source.payload_path) as parquet:
        rows = parquet.read().to_pylist()
    for row in rows:
        row.update(symbol="001914", ts_code="001914.SZ")
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    previous = AnnouncementCollection.model_validate_json(path.read_bytes())
    proof = b"%PDF-original code change notice 000043 to 001914"
    reference = pm.storage_root / "identity-reviews.json"
    reference.write_text(json.dumps([{"symbol": "001914", "published_symbol": "000043",
        "evidence_url": "https://static.cninfo.com.cn/finalpage/2019-12-16/99.PDF", "evidence_file": "code-change-99.pdf"}]))
    (reference.parent / "code-change-99.pdf").write_bytes(proof)
    document = previous.documents[0].model_copy(update={"symbol": "000043"})
    broker = Mock()
    broker.stock_ids.return_value = {"001914": "gssz0000043"}
    broker.company_index.return_value = (object(),)
    broker.download_distribution_documents.return_value = (document,)
    monkeypatch.setattr("src.data_system.steps.collect_announcements.CninfoBroker", Mock(return_value=broker))
    step = CollectAnnouncementsStep(pm, previous.announcement_start, previous.announcement_end,
        lambda: datetime(2026, 10, 18, tzinfo=timezone.utc), identity_refs=(reference,))
    step.run(context)
    assert broker.company_index.call_args.kwargs["historical_symbols"] == ("000043",)
    saved = AnnouncementCollection.model_validate_json(path.read_bytes())
    assert saved.documents[0].symbol == "000043"
    assert saved.index_counts == {"001914": 1}
    assert "code-change-99.pdf" in saved.request_files
    assert (path.parent / "code-change-99.pdf").read_bytes() == proof
    before = path.read_bytes()
    step.run(context)
    assert path.read_bytes() == before
