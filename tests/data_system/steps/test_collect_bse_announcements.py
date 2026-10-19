# filepath: tests/data_system/steps/test_collect_bse_announcements.py
"""BSE collection must retain old-code evidence and reuse only complete source coverage."""

from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import meta
from src.data_system.announcement_evidence import AnnouncementCollection, SecurityIdentity
from src.data_system.context import DataContext
from src.data_system.steps.collect_bse_announcements import CollectBseAnnouncementsStep
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager
from tests.fixtures.announcement_evidence import evidence


def test_old_and_current_codes_are_queried_without_fabricating_an_identity(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, _, _ = evidence
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    with pq.ParquetFile(source.payload_path) as parquet:
        rows = parquet.read().to_pylist()
    rows[0].update(symbol="920685", ts_code="920685.BJ")
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=source.payload_path)
    broker = Mock()

    def identities(*, directory: Path) -> tuple[SecurityIdentity, ...]:
        payload = b"official security-code mapping fixture"
        FileSystem.write_bytes_atomic(directory / "security-code-mapping.html", payload)
        FileSystem.write_bytes_atomic(directory / "security-code-mapping.request.json", b"{}")
        return (SecurityIdentity(symbol="920685", published_symbol="430685",
            evidence_url="https://www.bse.cn/service/code_mapping.html", evidence_sha256=sha256(payload).hexdigest()),)

    broker.security_identities.side_effect = identities
    broker.company_index.side_effect = [(SimpleNamespace(announcement_id="a" * 32),), ()]
    broker.download_distribution_documents.return_value = ()
    monkeypatch.setattr("src.data_system.steps.collect_bse_announcements.BseBroker", Mock(return_value=broker))
    step = CollectBseAnnouncementsStep(pm, date(2024, 1, 1), date(2026, 10, 17),
        lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    step.run(context)
    assert [call.kwargs["symbol"] for call in broker.company_index.call_args_list] == ["430685", "920685"]
    path = pm.raw_payload(broker="bse", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    assert collection.index_counts == {"920685": 1}
    assert collection.security_identities[0].published_symbol == "430685"
    before = path.read_bytes(), path.stat().st_mtime_ns
    step.run(context)
    assert broker.company_index.call_count == 2
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    restricted = collection.model_copy(update={"query_scope": {"isNewThree": "1"}})
    FileSystem.write_bytes_atomic(path, restricted.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    broker.company_index.side_effect = [(SimpleNamespace(announcement_id="a" * 32),), ()]
    step.run(context)
    assert broker.company_index.call_count == 4
    assert AnnouncementCollection.model_validate_json(path.read_bytes()).query_scope["isNewThree"] == ""
    archived, = (path.parent / "history").glob("*/announcements.json")
    assert AnnouncementCollection.model_validate_json(archived.read_bytes()).query_scope["isNewThree"] == "1"
    (path.parent / "security-code-mapping.html").write_bytes(b"modified alias evidence")
    with pytest.raises(ValueError, match="request evidence changed"):
        step.run(context)
