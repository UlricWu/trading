# filepath: tests/data_system/test_announcement_maintenance.py
"""Publication evidence remains actionable when its local candidate arrives later."""

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.announcement_maintenance import prepare_announcement_updates
from src.data_system.announcement_publications import AnnouncementPublication
from src.data_system.context import DataContext
from src.data_system.steps.collect_publications import CollectPublicationsStep
from src.utils.path import PathManager
from tests.fixtures.announcement_evidence import evidence


def test_previously_unmatched_publication_is_rejoined_when_local_source_arrives(
    evidence: tuple[PathManager, DataContext, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, context, _, _ = evidence
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    original, = AnnouncementCollection.model_validate_json(path.read_bytes()).documents
    row = AnnouncementPublication(source="cninfo", **original.model_dump(exclude={"sha256", "fetched_at"}))
    row = row.model_copy(update={"title": "权益分派实施公告", "published_date": date(2026, 9, 19)})
    brokers = {"cninfo": Mock(), "bse": Mock()}
    for name, broker in brokers.items():
        broker.announcement_index.return_value = (row,) if name == "cninfo" else ()
        monkeypatch.setattr(f"src.data_system.steps.collect_publications.{name.capitalize()}Broker", Mock(return_value=broker))
    step = CollectPublicationsStep(pm, lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    step.run(DataContext(start="2026-09-19", end="2026-09-19"))
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    originals = {p: p.read_bytes() for p in (source.payload_path, source.meta_path)}
    for p in originals:
        p.unlink()
    first = prepare_announcement_updates(path_manager=pm, start=date(2026, 9, 19), end=date(2026, 9, 19))
    assert first.unmatched_publications == (row,)
    assert first.refreshes == ()
    for p, contents in originals.items():
        p.write_bytes(contents)
    for broker in brokers.values():
        broker.announcement_index.return_value = ()
    step.run(DataContext(start="2026-10-18", end="2026-10-18"))
    later = prepare_announcement_updates(path_manager=pm, start=date(2026, 10, 18), end=date(2026, 10, 18))
    refresh, = later.refreshes
    assert later.unmatched_publications == ()
    assert refresh.registration_date == context.start
    assert refresh.publications == (row,)
    assert refresh.announcement_end == date(2026, 10, 17)
