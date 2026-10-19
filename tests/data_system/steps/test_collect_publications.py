# filepath: tests/data_system/steps/test_collect_publications.py
"""Publication discovery covers weekends and preserves earlier source observations."""

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.access import meta
from src.data_system.announcement_publications import AnnouncementPublication, PublicationCoverage, PublicationIndex
from src.data_system.context import DataContext
from src.data_system.steps.collect_publications import CollectPublicationsStep
from src.utils.path import PathManager


def test_changed_index_is_archived_and_identical_content_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = PathManager(tmp_path)
    row = AnnouncementPublication(source="cninfo", announcement_id="1", symbol="002986",
        title="关于相关公告更正的说明", published_date="2026-09-19",
        url="https://static.cninfo.com.cn/finalpage/2026-09-19/1.PDF")
    cninfo, bse = Mock(), Mock()
    cninfo.announcement_index.return_value = (row,)
    bse.announcement_index.return_value = ()
    monkeypatch.setattr("src.data_system.steps.collect_publications.CninfoBroker", Mock(return_value=cninfo))
    monkeypatch.setattr("src.data_system.steps.collect_publications.BseBroker", Mock(return_value=bse))
    step = CollectPublicationsStep(pm, lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    context = DataContext(start="2026-09-19", end="2026-09-19")
    step.run(context)
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_publications",
        trade_date=context.start, payload_file="index.json")
    original = path.read_bytes(), path.stat().st_mtime_ns
    step.run(context)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original
    assert cninfo.announcement_index.call_count == bse.announcement_index.call_count == 2
    assert cninfo.announcement_index.call_args.kwargs["start"] == date(2026, 9, 19)
    changed = row.model_copy(update={"announcement_id": "2", "url": row.url.replace("/1.PDF", "/2.PDF")})
    cninfo.announcement_index.return_value = (row, changed)
    step.run(context)
    archived, = (path.parent / "history").glob("*/index.json")
    assert archived.read_bytes() == original[0]
    assert len(PublicationIndex.model_validate_json(path.read_bytes()).announcements) == 2
    coverage = PublicationCoverage.model_validate_json((path.parent / "coverage.json").read_bytes())
    assert coverage.index_count == 2
    assert meta.require(pm=pm, meta_path=path.parent / "meta.json").payload_path == path
    before = path.read_bytes()
    cninfo.announcement_index.side_effect = ValueError("incomplete pagination")
    with pytest.raises(ValueError, match="incomplete pagination"):
        step.run(context)
    assert path.read_bytes() == before


@pytest.mark.parametrize("provider", ["cninfo", "bse"])
@pytest.mark.parametrize("previously_committed", [False, True])
def test_later_window_retries_an_incomplete_earlier_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, previously_committed: bool,
) -> None:
    pm = PathManager(tmp_path)
    brokers = {"cninfo": Mock(), "bse": Mock()}
    for name, broker in brokers.items():
        broker.announcement_index.return_value = ()
        monkeypatch.setattr(f"src.data_system.steps.collect_publications.{name.capitalize()}Broker", Mock(return_value=broker))
    step = CollectPublicationsStep(pm, lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    earlier = DataContext(start="2026-09-19", end="2026-09-19")
    if previously_committed:
        step.run(earlier)
    brokers[provider].announcement_index.side_effect = ValueError("incomplete pagination")
    with pytest.raises(ValueError, match="incomplete pagination"):
        step.run(earlier)
    for broker in brokers.values():
        broker.reset_mock()
        broker.announcement_index.side_effect = None
    step.run(DataContext(start="2026-09-20", end="2026-09-20"))
    for broker in brokers.values():
        assert [call.kwargs["start"] for call in broker.announcement_index.call_args_list] == [
            date(2026, 9, 19), date(2026, 9, 20),
        ]
    assert not list(tmp_path.glob("raw/*/corporate_action_publications/trade_date=*/pending.json"))
