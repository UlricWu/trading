# filepath: tests/data_system/test_announcement_facts.py
"""Partial disclosures remain useful without borrowing facts from later documents."""

from datetime import datetime, timezone
from decimal import Decimal

from src.data_system.announcement_facts import assemble_announcement_events, extract_announcement_facts
from src.data_system.distribution_announcements import AnnouncementText


def test_registration_pool_and_later_ratio_are_separate_public_versions() -> None:
    first = AnnouncementText(source="cninfo", announcement_id="1", symbol="600518",
        title="关于资本公积转增股本的实施公告", published_date="2021-12-10",
        url="https://example.test/1.pdf", pages=("证券代码：600518 股权登记日：2021年12月14日。"
        "1113309515股股票将向股权登记日登记在册的前五十名股东之外的全体股东进行分配，"
        "按持股数量的相对比例分配。本次不派发现金红利。",))
    observed = datetime(2026, 10, 28, tzinfo=timezone.utc)
    early = extract_announcement_facts(first, observed_at=observed, previous=())
    assert early.shares_numerator == 1113309515
    assert early.shares_denominator is None and early.shares_per_share is None
    assert early.cash_per_share == 0
    assert early.record_date.isoformat() == "2021-12-14"
    second = first.model_copy(update={"announcement_id": "2", "published_date": first.published_date.replace(day=20),
        "title": "转增股本补充公告", "url": "https://example.test/2.pdf",
        "pages": ("证券代码：600518 关于2021年12月10日披露的《关于资本公积转增股本的实施公告》，"
        "符合条件的普通股东实际每10股获配5.8267股。",)})
    later = extract_announcement_facts(second, observed_at=observed, previous=(early,))
    assert later.event_id == early.event_id == "cninfo:1"
    before, = assemble_announcement_events((early,))
    after, = assemble_announcement_events((early, later, later))
    assert before.shares_per_share is None
    assert after.shares_per_share == Decimal("0.58267")
    assert after.cash_per_share == 0 and after.record_date == early.record_date
    assert after.shares_denominator is None
    assert after.evidence_refs == (first.url, second.url)


def test_same_issuer_does_not_link_another_plan() -> None:
    document = AnnouncementText(source="cninfo", announcement_id="1", symbol="600518",
        title="重整进展公告", published_date="2021-12-10", url="https://example.test/1.pdf",
        pages=("证券代码：600518 重整方案尚需审议，分配比例待确定。",))
    observed = datetime(2026, 10, 28, tzinfo=timezone.utc)
    first = extract_announcement_facts(document, observed_at=observed, previous=())
    other = extract_announcement_facts(document.model_copy(update={"announcement_id": "2"}),
        observed_at=observed, previous=(first,))
    assert len(assemble_announcement_events((first, other))) == 2
    assert first.benefit_state == "conditional" and first.shares_per_share is None


def test_omission_conflict_correction_and_cancellation_keep_distinct_meanings() -> None:
    document = AnnouncementText(source="cninfo", announcement_id="1", symbol="600518",
        title="重整实施公告", published_date="2021-12-10", url="https://example.test/1.pdf",
        pages=("证券代码：600518 股权登记日：2021年12月14日。普通股东实际每10股获配5股。不派发现金红利。",))
    first = extract_announcement_facts(document, observed_at=datetime(2026, 10, 28, tzinfo=timezone.utc), previous=())
    second = first.model_copy(update={"publication": first.publication.model_copy(update={"announcement_id": "2",
        "published_date": first.publication.published_date.replace(day=11)}), "shares_per_share": Decimal("0.6"),
        "cash_per_share": None, "record_date": None, "evidence_refs": ("https://example.test/2.pdf",)})
    conflict, = assemble_announcement_events((first, second))
    assert conflict.conflicts == ("shares_per_share",) and conflict.shares_per_share == Decimal("0.5")
    assert conflict.cash_per_share == 0 and conflict.record_date == first.record_date
    correction = second.model_copy(update={"is_revision": True, "publication": second.publication.model_copy(update={
        "announcement_id": "3", "published_date": first.publication.published_date.replace(day=12)})})
    fixed, = assemble_announcement_events((first, second, correction))
    assert not fixed.conflicts and fixed.shares_per_share == Decimal("0.6")
    simultaneous = correction.model_copy(update={"publication": correction.publication.model_copy(update={
        "published_date": second.publication.published_date})})
    uncertain, = assemble_announcement_events((first, second, simultaneous))
    assert uncertain.conflicts == ("shares_per_share",)
    cancelled = correction.model_copy(update={"benefit_state": "cancelled", "shares_per_share": None,
        "publication": correction.publication.model_copy(update={"announcement_id": "4",
            "published_date": first.publication.published_date.replace(day=13)})})
    ended, = assemble_announcement_events((first, second, correction, cancelled))
    assert ended.benefit_state == "cancelled" and ended.shares_per_share == Decimal("0.6")
    assert assemble_announcement_events((first,))[0].benefit_state == "confirmed"
