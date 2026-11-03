# filepath: tests/data_system/test_announcement_publications.py
"""Announcement selection finds business topics without interpreting document status as event cancellation."""

import pytest
from datetime import datetime

from src.data_system.announcement_publications import AnnouncementPublication, is_distribution_evidence


@pytest.mark.parametrize("title,expected", [
    ("关于重整计划资本公积转增股本事项实施的提示性公告", True),
    ("关于重整计划资本公积金转增股本事项实施暨风险提示的公告", True),
    ("关于业绩补偿股份转赠进展的公告", True),
    ("2020年年度审计报告（已取消）", False),
    ("2020年年度审计报告(已取消)", False),
    ("关于取消本次权益分派的公告", True),
    ("关于调整可转债转股价格的公告", False),
])
def test_distribution_topics_are_independent_of_implementation_title_forms(title: str, expected: bool) -> None:
    assert is_distribution_evidence(title) is expected


def test_publication_visibility_uses_exact_time_or_the_next_natural_day() -> None:
    publication = AnnouncementPublication(source="cninfo", announcement_id="1", symbol="600518",
        title="重整进展公告", published_date="2026-09-19", url="https://example.test/1.pdf")
    next_day = int(datetime.fromisoformat("2026-09-20T00:00:00+08:00").timestamp()) * 1_000_000
    assert publication.available_ts_us == next_day
    exact = next_day - 3_600_000_000
    timed = AnnouncementPublication.model_validate({**publication.model_dump(), "published_ts_us": exact})
    assert timed.available_ts_us == exact
    with pytest.raises(ValueError, match="exact publication time"):
        AnnouncementPublication.model_validate({**publication.model_dump(), "published_ts_us": next_day})
