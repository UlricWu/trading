# filepath: tests/data_system/test_distribution_announcements.py
"""Public announcement facts must distinguish entitlements, references and revisions."""

from datetime import date
from decimal import Decimal

import pytest

from src.data_system.corporate_actions import DividendRecord
from src.data_system.distribution_announcements import AnnouncementText, extract_distribution_review


def _source() -> DividendRecord:
    return DividendRecord(ts_code="600000.SH", symbol="600000", end_date="2026-06-30", ann_date=None,
        div_proc="实施", imp_ann_date="2026-09-01", record_date="2026-09-09", ex_date="2026-09-10",
        pay_date="2026-09-10", div_listdate=None, cash_div_tax="0.1", stk_div="0", source_row_id="1" * 64)


def _document(*, plan: str, reference: str, publication: str = "2026-09-01", identity: str = "1") -> AnnouncementText:
    return AnnouncementText(announcement_id=identity, symbol="600000", title="2026年半年度权益分派实施公告",
        published_date=publication, url=f"https://static.cninfo.com.cn/finalpage/{publication}/{identity}.PDF",
        sha256=identity * 64, pages=(f"""证券代码：600000
二、分配方案
{plan}
三、相关日期
股权登记日为：2026年9月9日，除权除息日为：2026年9月10日。
现金红利将于2026年9月10日发放。
新增可流通股份上市日：2026年9月10日。
登记在册的本公司全体股东。
{reference}""",))


def test_entitlement_does_not_copy_ex_reference_or_after_tax_amount() -> None:
    document = _document(plan="向全体股东每10股派1.002381元人民币现金（含税；扣税后每10股派0.9021429元）。",
                         reference="除权除息价格=股权登记日收盘价-0.0999693元/股。")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None
    terms, = result.review.terms
    assert terms.cash_per_share == Decimal("0.1002381")
    assert terms.reference_cash_per_share == Decimal("0.0999693")
    assert result.review.evidence_refs == (document.url + "#sha256=" + document.sha256,)


def test_stock_reference_is_independent_of_received_shares() -> None:
    document = _document(plan="向全体股东每10股转增4股，不派发现金红利。",
                         reference="除权除息价格=股权登记日收盘价/（1+0.3901769）。")
    review = extract_distribution_review((_source(),), (document,)).review
    assert review is not None
    assert review.pay_date is None
    assert review.div_listdate == date(2026, 9, 10)
    assert review.terms[0].shares_per_share == Decimal("0.4")
    assert review.terms[0].reference_shares_per_share == Decimal("0.3901769")


@pytest.mark.parametrize(("plan", "reference", "reason"), [
    ("每10股转增4股。", "", "zero_cash_not_confirmed"),
    ("每10股派1元人民币现金（含税）。", "", "ex_reference_not_confirmed"),
    ("每10股派1元人民币现金（含税）。每10股派2元人民币现金（含税）。", "", "distribution_amount_ambiguous"),
    ("每10股派1元人民币现金（含税）。", "重整投资人另行受让。", "special_beneficiary_requires_review"),
    ("每10股派1元人民币现金（含税）。", "除权除息价格=前收盘价-0.08元。另：除权除息价格=前收盘价-0.09元。", "ex_reference_not_confirmed"),
])
def test_ambiguous_or_incomplete_pdf_is_not_approved(plan: str, reference: str, reason: str) -> None:
    result = extract_distribution_review((_source(),), (_document(plan=plan, reference=reference),))
    assert result.review is None
    assert result.reason.startswith(reason)


def test_versions_are_retained_and_phase_date_correction_does_not_backfill_history() -> None:
    first = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    revised = _document(plan="每10股派2元人民币现金（含税）。", reference="差异化分红送转：否",
                        publication="2026-09-08", identity="2")
    review = extract_distribution_review((_source(),), (revised, first)).review
    assert review is not None
    assert [term.cash_per_share for term in review.terms] == [Decimal("0.1"), Decimal("0.2")]
    corrected = revised.model_copy(update={"pages": (revised.pages[0].replace(
        "现金红利将于2026年9月10日", "现金红利将于2026年9月11日"),)})
    result = extract_distribution_review((_source(),), (first, corrected))
    assert result.review is None
    assert result.reason == "phase_date_revision_requires_review"


def test_b_share_date_row_cannot_replace_a_share_dates() -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    document = document.model_copy(update={"pages": (document.pages[0] + "\nB股 2026/9/11 2026/9/10 2026/9/15",)})
    review = extract_distribution_review((_source(),), (document,)).review
    assert review is not None
    assert review.record_date == date(2026, 9, 9)
    assert review.pay_date == date(2026, 9, 10)


def test_convertible_bond_capital_change_clause_is_not_a_restructuring() -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。如因可转债转股变动，维持分配比例。",
                         reference="差异化分红送转：否")
    assert extract_distribution_review((_source(),), (document,)).review is not None


def test_separate_cancellation_or_correction_cannot_be_ignored() -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    correction = document.model_copy(update={"announcement_id": "2", "title": "关于权益分派的更正公告",
        "published_date": date(2026, 9, 8), "pages": ("取消2026年9月9日股权登记的权益分派安排。",)})
    result = extract_distribution_review((_source(),), (document, correction))
    assert result.review is None
    assert result.reason == "separate_revision_requires_review:2"
