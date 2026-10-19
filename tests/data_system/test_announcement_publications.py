# filepath: tests/data_system/test_announcement_publications.py
"""Publication discovery distinguishes potential revisions from approved distributions."""

import pytest

from src.data_system.announcement_publications import AnnouncementPublication, PublicationIndex, is_distribution_evidence


def test_generic_termination_is_kept_for_later_event_review() -> None:
    assert is_distribution_evidence("关于终止相关事项的公告")
    assert is_distribution_evidence("关于相关公告更正的说明")
    assert not is_distribution_evidence("关于调整可转债转股价格的公告")


def test_index_cannot_claim_another_source_or_publication_day() -> None:
    row = AnnouncementPublication(source="cninfo", announcement_id="1", symbol="600000", title="更正公告",
        published_date="2026-09-19", url="https://static.cninfo.com.cn/finalpage/2026-09-19/1.PDF")
    with pytest.raises(ValueError, match="outside its source or day"):
        PublicationIndex(version="v1", source="cninfo", publication_date="2026-09-20", announcements=(row,))
    with pytest.raises(ValueError, match="outside its source or day"):
        PublicationIndex(version="v1", source="bse", publication_date="2026-09-19", announcements=(row,))
    with pytest.raises(ValueError, match="duplicate"):
        PublicationIndex(version="v1", source="cninfo", publication_date="2026-09-19", announcements=(row, row))
