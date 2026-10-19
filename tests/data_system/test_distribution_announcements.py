# filepath: tests/data_system/test_distribution_announcements.py
"""Public announcement facts must distinguish entitlements, references and revisions."""

from datetime import date
from decimal import Decimal

import pytest

from src.data_system.corporate_actions import DividendRecord
from src.data_system.announcement_evidence import SecurityIdentity
from src.data_system.distribution_announcements import AnnouncementText, extract_distribution_review


def _source() -> DividendRecord:
    return DividendRecord(ts_code="600000.SH", symbol="600000", end_date="2026-06-30", ann_date=None,
        div_proc="实施", imp_ann_date="2026-09-01", record_date="2026-09-09", ex_date="2026-09-10",
        pay_date="2026-09-10", div_listdate=None, cash_div_tax="0.1", stk_div="0", source_row_id="1" * 64)


def _document(*, plan: str, reference: str, publication: str = "2026-09-01", identity: str = "1") -> AnnouncementText:
    return AnnouncementText(source="cninfo", announcement_id=identity, symbol="600000", title="2026年半年度权益分派实施公告",
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


@pytest.mark.parametrize("bonus,capital,total", [("1", "4", "0.5"), ("1", "1", "0.2")])
def test_bonus_and_capital_shares_are_distinct_components(bonus: str, capital: str, total: str) -> None:
    document = _document(plan=f"以公司现有总股本100000股为基数，每10股送红股{bonus}股，派0.3元人民币现金（含税）；"
        f"同时以资本公积金向全体股东每10股转增{capital}股。每10股送红股{bonus}股。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    terms, = result.review.terms
    assert terms.cash_per_share == Decimal("0.03")
    assert terms.shares_per_share == Decimal(total)
    assert terms.reference_shares_per_share == Decimal(total)
    conflicting = document.model_copy(update={"pages": (document.pages[0].replace(
        "三、相关日期", "每10股送红股2股。三、相关日期"),)})
    assert extract_distribution_review((_source(),), (conflicting,)).reason.startswith("distribution_amount_ambiguous")


def test_numbered_first_implementation_section_excludes_original_proposal() -> None:
    document = _document(plan="以公司现有总股本373316347股为基数，向全体股东每10股派1.000038元人民币现金（含税）。",
        reference="")
    text = document.pages[0].replace("二、分配方案", "股东大会原方案每10股派1.00元人民币现金（含税）。一、权益分派方案")
    text = text.replace("三、相关日期", "二、股权登记日与除权除息日")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.1000038")
    assert result.review.terms[0].reference_cash_per_share == Decimal("0.1000038")


def test_combined_cash_and_capital_section_excludes_prior_cash_proposal() -> None:
    document = _document(plan="以公司现有总股本100000股为基数，每10股派2元人民币现金（含税）。"
        "每10股转增4股，不送红股。", reference="")
    text = document.pages[0].replace("二、分配方案", "一、股东会审议通过的原方案\n"
        "每10股派1元人民币现金（含税）。二、本次实施的利润分配及资本公积金转增股本方案")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.2")
    assert result.review.terms[0].shares_per_share == Decimal("0.4")


@pytest.mark.parametrize("capital", ["资本公积金", "资本公积"])
def test_capital_shares_described_as_sent_are_not_an_additional_bonus(capital: str) -> None:
    document = _document(plan="以公司现有总股本100000股为基数，每10股派3元人民币现金（含税），"
        "同时以资本公积金向全体股东每10股转增4股。", reference="")
    text = document.pages[0].replace("二、分配方案", "一、股东会审议通过的原方案\n"
        f"每10股派3元人民币现金（含税）；以{capital}每10股送4股，合计转增40000股，本次不送红股。"
        "二、本次实施的利润分配及资本公积金转增股本方案")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].shares_per_share == Decimal("0.4")
    assert result.review.terms[0].reference_shares_per_share == Decimal("0.4")


@pytest.mark.parametrize("capital", ["资本公积金", "资本公积"])
def test_capital_shares_described_as_sent_remain_a_distribution(capital: str) -> None:
    document = _document(plan="以公司现有总股本100000股为基数，每10股派3元人民币现金（含税），"
        f"以{capital}每10股送4股。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].shares_per_share == Decimal("0.4")
    assert result.review.terms[0].reference_shares_per_share == Decimal("0.4")


def test_named_final_dividend_cannot_be_replaced_by_paid_interim_cash() -> None:
    document = _document(plan="以公司现有总股本100000股为基数，每股派发2026年末期现金红利人民币0.22元（含税）。"
        "公司已派发2026年中期现金红利每股人民币0.30元（含税）。", reference="差异化分红送转：否")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("distribution_amount_ambiguous")


def test_total_share_base_can_be_stated_as_benchmark() -> None:
    document = _document(plan="以公司总股本3124191896股为基准，向全体股东每10股派2元人民币现金（含税）。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].reference_cash_per_share == Decimal("0.2")


@pytest.mark.parametrize("base", [
    "公司现有股本总数415,718,940股", "公司现有股本总额415,718,940股",
    "公司现有总股本52,800万股", "公司现有总股本100,574.3085万股", "公司现有总股本1,517,828,334",
])
def test_explicit_full_capital_wording_preserves_per_share_reference(base: str) -> None:
    document = _document(plan=f"以{base}为基数，向全体股东每10股派0.8元人民币现金（含税）。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.08")
    assert result.review.terms[0].reference_cash_per_share == Decimal("0.08")


@pytest.mark.parametrize("base", [
    "公司可分配股数52,800万股", "公司总股本53,000万股扣除回购股份后的52,800万股",
    "公司现有股本总数53,000万股（不含回购账户200万股）",
])
def test_partial_capital_base_does_not_establish_uniform_reference(base: str) -> None:
    document = _document(plan=f"以{base}为基数，向全体股东每10股派0.8元人民币现金（含税）。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("ex_reference_not_confirmed")


@pytest.mark.parametrize("qualification", ["已扣减", "扣除", "剔除", "不含"])
def test_qualified_full_capital_base_requires_independent_reference(qualification: str) -> None:
    document = _document(plan=f"以公司现有总股本52,800万股为基数（{qualification}回购账户200万股），"
        "向全体股东每10股派0.8元人民币现金（含税）。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("ex_reference_not_confirmed")


@pytest.mark.parametrize("plan,cash", [
    ("每10股派送2.00元人民币现金红利（含税）。", "0.2"),
    ("每10股派0.600000元人民币现金[含税；扣税后每10股派0.540000元]。", "0.06"),
])
def test_explicit_gross_cash_typography_keeps_tax_basis(plan: str, cash: str) -> None:
    document = _document(plan=plan, reference="差异化分红送转：否")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal(cash)
    after_tax = document.model_copy(update={"pages": (document.pages[0].replace("含税", "扣税后"),)})
    assert extract_distribution_review((_source(),), (after_tax,)).review is None


@pytest.mark.parametrize("plan,cash", [
    ("每股派发现金红利人民币0.261元（税前）。", "0.261"),
    ("每10股派发人民币现金红利0.600000元[税前；扣税后每10股派0.540000元]。", "0.06"),
])
def test_explicit_before_tax_amount_keeps_gross_cash(plan: str, cash: str) -> None:
    document = _document(plan=plan, reference="差异化分红送转：否")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal(cash)
    assert result.review.terms[0].reference_cash_per_share == Decimal(cash)


@pytest.mark.parametrize("qualifier", ["税后", "扣税后", "不含税", "税前金额另行确定", "税务口径未确认"])
def test_uncertain_or_after_tax_amount_is_not_gross_cash(qualifier: str) -> None:
    document = _document(plan=f"每股派发现金红利人民币0.261元（{qualifier}）。", reference="差异化分红送转：否")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("distribution_amount_missing")


@pytest.mark.parametrize("headline", ["A股每股现金分红人民币0.30元（税前）", "A股每股现金红利人民币0.30元（税前）"])
def test_bank_gross_headline_must_agree_with_implementation_cash(headline: str) -> None:
    document = _document(plan="每股派发现金红利人民币0.25元（含税）。", reference="差异化分红送转：否")
    text = document.pages[0].replace("二、分配方案", f"重要内容提示：{headline}。二、分配方案")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is None
    assert result.reason.startswith("distribution_amount_ambiguous")


def test_gross_tax_synonyms_cannot_hide_conflicting_amounts() -> None:
    document = _document(plan="每股派发现金红利人民币0.261元（税前）。每股派发现金红利人民币0.30元（含税）。",
        reference="差异化分红送转：否")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("distribution_amount_ambiguous")


@pytest.mark.parametrize("share_class", ["A股", ""])
@pytest.mark.parametrize("headline,plan,approved", [("0.30", "0.25", False), ("0.30", "0.30", True)])
def test_headline_must_agree_with_implementation_cash(share_class: str, headline: str, plan: str, approved: bool) -> None:
    document = _document(plan=f"向全体股东派发现金红利每股{plan}元（含税）。",
        reference="差异化分红送转：是。除权除息参考价格=（前收盘价格-0.2974）÷（1+0）。")
    text = document.pages[0].replace("二、分配方案", f"重要内容提示：{share_class}每股现金红利{headline}元。二、分配方案")
    text += "扣税后实际派发现金红利每股人民币0.27元。"
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    if approved:
        assert result.review is not None, result.reason
        assert result.review.terms[0].cash_per_share == Decimal(plan)
        assert result.review.terms[0].reference_cash_per_share == Decimal("0.2974")
    else:
        assert result.review is None
        assert result.reason.startswith("distribution_amount_ambiguous")


@pytest.mark.parametrize("share_class", ["B股", "H股"])
def test_foreign_share_class_summary_is_not_an_a_share_cash_conflict(share_class: str) -> None:
    document = _document(plan="每股派发现金红利0.30元（含税）。", reference="差异化分红送转：否")
    text = document.pages[0].replace("二、分配方案",
        f"重要内容提示：A股每股现金红利0.30元，{share_class}每股现金红利0.25元。二、分配方案")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.30")


@pytest.mark.parametrize("per_ten,approved", [("2.599999", False), ("2.599990", True)])
def test_actual_per_share_equation_cannot_disagree_with_per_ten_distribution(per_ten: str, approved: bool) -> None:
    document = _document(plan="本次实际每股现金红利=现金分红总额÷总股本，"
        "即0.259999元/股=179402901.60元/690011220股。"
        f"向全体股东每10股派{per_ten}元人民币现金（含税）。",
        reference="除权（息）参考价格=前收盘价格-0.259999元。")
    result = extract_distribution_review((_source(),), (document,))
    if approved:
        assert result.review is not None, result.reason
        assert result.review.terms[0].cash_per_share == Decimal("0.259999")
    else:
        assert result.review is None
        assert result.reason.startswith("distribution_amount_ambiguous")


def test_after_tax_body_amount_is_not_a_gross_cash_summary() -> None:
    document = _document(plan="每股派发现金红利0.30元（含税）。", reference="差异化分红送转：否")
    text = document.pages[0] + "对于QFII股东，扣税后每股现金红利0.27元。"
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.30")


@pytest.mark.parametrize("summary", [
    "本次权益分派实施后的除权除息价格=股权登记日收盘价-按总股本折算每股现金红利0.2974元。",
    "计算除权除息价格时，按总股本折算每10股现金红利为2.974元，即每股现金红利为0.2974元。",
])
def test_reference_cash_in_summary_is_distinct_from_distribution(summary: str) -> None:
    document = _document(plan="每股派发现金红利0.30元（含税）。",
        reference="除权除息参考价格=前收盘价格-0.2974元。")
    text = document.pages[0].replace("二、分配方案", f"特别提示：{summary}二、分配方案")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.30")
    assert result.review.terms[0].reference_cash_per_share == Decimal("0.2974")


@pytest.mark.parametrize("title", ["权益分派实施公告", "分红派息、转增股本实施公告"])
def test_undated_title_uses_an_explicit_consistent_plan_period(title: str) -> None:
    document = _document(plan="本公司2026年半年度权益分派方案为：每10股派1元人民币现金（含税）。",
        reference="差异化分红送转：否").model_copy(update={"title": title})
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.1")


@pytest.mark.parametrize("title,period_text", [
    ("2025年半年度权益分派实施公告", "本公司2026年半年度权益分派方案为："),
    ("权益分派实施公告", "本公司2025年半年度权益分派方案为："),
    ("权益分派实施公告", "本公司2026年第一季度权益分派方案为："),
    ("权益分派实施公告", "公司2026年9月召开股东会。本次方案为："),
    ("权益分派实施公告", "此前2025年半年度权益分派方案已完成。本公司2026年半年度权益分派方案为："),
])
def test_title_or_body_period_ambiguity_cannot_be_replaced_by_source_period(title: str, period_text: str) -> None:
    document = _document(plan=f"{period_text}每10股派1元人民币现金（含税）。",
        reference="差异化分红送转：否").model_copy(update={"title": title})
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason == "missing_implementation"


@pytest.mark.parametrize("registration", ["股权登记日（2026年9月9日）", "本次权益分派登记日为：2026年9月9日"])
def test_registration_date_uses_explicit_parenthesis_or_distribution_label(registration: str) -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    text = document.pages[0].replace("股权登记日为：2026年9月9日", registration)
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.record_date == date(2026, 9, 9)


@pytest.mark.parametrize("header", ["测试股份2026年公告", "测试股份有限公司"])
def test_repeated_company_header_does_not_split_date_or_shareholder_word(header: str) -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    text = document.pages[0]
    first, last = text.split("2026年9月10日", maxsplit=1)
    middle, end = last.split("登记在册", maxsplit=1)
    pages = (header + "\n1\n" + first + "2026年9", header + "\n2\n月10日" + middle + "登",
        header + "\n3\n记在册" + end)
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": pages}),))
    assert result.review is not None, result.reason
    assert result.review.ex_date == date(2026, 9, 10)


def test_cash_payment_accepts_explicit_a_share_dividend_label() -> None:
    document = _document(plan="每10股派1.2元人民币现金（含税）。", reference="差异化分红送转：否")
    text = document.pages[0].replace("现金红利将于", "A股股东红利将于")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.pay_date == date(2026, 9, 10)


def test_security_code_is_checked_in_identified_header_even_after_pdf_body() -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    text = document.pages[0].replace("证券代码：600000", "") + "公司实施情况。" * 150
    document = document.model_copy(update={"pages": (text + "证券代码：600000 证券简称：测试股份",)})
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    wrong = document.model_copy(update={"pages": ("交易金额600000元。" + text + "证券代码：600001",)})
    assert extract_distribution_review((_source(),), (wrong,)).reason.startswith("pdf_security_identity_mismatch")


@pytest.mark.parametrize("bonus,capital,total", [("5", "0", "0.5"), ("1", "2", "0.3")])
def test_cash_before_bonus_preserves_the_shared_per_ten_share_unit(bonus: str, capital: str, total: str) -> None:
    document = _document(plan="按公司总股本534948992股为基数，每10股派送1.1元（含税）现金红利，"
        f"送红股{bonus}股，以资本公积向全体股东每10股转增{capital}股。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.11")
    assert result.review.terms[0].shares_per_share == Decimal(total)
    assert result.review.div_listdate == date(2026, 9, 10)


@pytest.mark.parametrize("cash_clause", ["每10股派5.000000元人民币现金（含税）", "每10股派发现金红利5元（含税）"])
def test_bonus_after_cash_and_separate_capital_allocation_are_both_counted(cash_clause: str) -> None:
    document = _document(plan=f"以公司现有总股本1600000000股为基数，{cash_clause}，送红股4股（含税），"
        "以资本公积金向全体股东每10股转增1股。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    terms, = result.review.terms
    assert terms.cash_per_share == terms.reference_cash_per_share == Decimal("0.5")
    assert terms.shares_per_share == terms.reference_shares_per_share == Decimal("0.5")
    assert result.review.div_listdate == date(2026, 9, 10)


def test_cash_adjustment_uses_the_complete_explicit_replacement_plan() -> None:
    document = _document(plan="原股东大会方案为每10股派发现金股利人民币0.48元（含税）。"
        "本公司将年度权益分派方案调整为：以公司现有总股本550787263股为基数，"
        "向全体股东每10股派发0.486311元人民币现金股利（含税）。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.0486311")
    assert result.review.terms[0].reference_cash_per_share == Decimal("0.0486311")


def test_unreadable_replacement_amount_cannot_fall_back_to_the_proposal() -> None:
    document = _document(plan="以公司总股本459979121股为基数，每10股派发现金红利1.5元（含税）。"
        "调整后的年度权益分派方案为：以公司现有总股本459987928股为基数，"
        "向全体股东每10股派元人民币现金1.499971（含税）。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("distribution_amount_missing")


@pytest.mark.parametrize("actual", [
    "每股实际派发现金红利0.09782元（含税）。",
    "调整后的每股现金红利=原定拟派发现金红利总额÷实际参与分配的股本数=17391680.67元÷237481686股=0.07323元/股（含税）。",
])
def test_different_actual_distribution_cannot_silently_keep_the_proposal(actual: str) -> None:
    document = _document(plan="公司原方案每10股派发现金红利人民币0.9684元（含税）。" + actual,
        reference="除权（息）参考价格=前收盘价格-0.09782元。")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("distribution_amount_ambiguous")


def test_unchanged_first_section_cannot_lose_shares_in_second_section() -> None:
    document = _document(plan="以公司现有总股本2841725474股为基数，每10股派4元人民币现金（含税）。", reference="")
    text = document.pages[0].replace("二、分配方案", "一、权益分派方案\n"
        "每10股派发现金股利4元（含税），向全体股东每10股转增3股，不送红股。"
        "本次实施的方案与股东大会通过的方案一致。二、权益分派方案")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].shares_per_share == Decimal("0.3")


@pytest.mark.parametrize("previous", ["每10股送红股4股。", "每10股转增4股。"])
def test_omitted_implementation_share_amount_cannot_become_zero(previous: str) -> None:
    document = _document(plan="以公司现有总股本72586668股为基数，每10股派4元人民币现金（含税）。", reference="")
    text = document.pages[0].replace("二、分配方案", "一、股东大会审议通过的利润分配方案情况\n"
        "每10股派发现金股利4元（含税）。" + previous +
        "本次实施的方案与股东大会通过的方案一致。二、本次实施的利润分配方案")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is None
    assert result.reason.startswith("distribution_amount_missing")


def test_explicit_cancellation_distinguishes_implementation_from_old_share_proposal() -> None:
    document = _document(plan="以公司现有总股本72586668股为基数，每10股派4元人民币现金（含税）。"
        "不送红股，不进行资本公积金转增股本。", reference="")
    text = document.pages[0].replace("二、分配方案", "一、股东大会原方案\n每10股送红股1股，每10股转增4股。"
        "调整后的方案为以下实施条款。二、本次实施的利润分配方案")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].shares_per_share == Decimal("0")


def test_no_bonus_statement_does_not_cancel_an_omitted_capital_distribution() -> None:
    document = _document(plan="以公司现有总股本72586668股为基数，每10股派4元人民币现金（含税）。不送红股。", reference="")
    text = document.pages[0].replace("二、分配方案", "一、股东大会原方案\n每10股转增4股。"
        "本次实施的方案与股东大会通过的方案一致。二、本次实施的利润分配方案")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is None
    assert result.reason.startswith("distribution_amount_missing")


def test_explicit_a_share_code_header_identifies_the_mainland_security() -> None:
    document = _document(plan="每股派发现金红利0.06374元（含税）。", reference="差异化分红送转：否")
    text = document.pages[0].replace("证券代码：600000", "A股代码：600000 A股简称：测试股份\nH股代码：00390")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.06374")
    other = text.replace("A股代码：600000", "A股代码：600001") + "交易金额600000元"
    assert extract_distribution_review((_source(),), (document.model_copy(update={"pages": (other,)}),)).review is None


@pytest.mark.parametrize("position", ["start", "end"])
def test_consecutive_page_fractions_cannot_enter_the_distribution_amount(position: str) -> None:
    document = _document(plan="每10股派发现金股利1.500000元（含税）。", reference="差异化分红送转：否")
    first, last = document.pages[0].split("1.500000", maxsplit=1)
    pages = (first + "1.500000", last)
    if position == "start":
        pages = tuple(f"{number} / 2\n{page}" for number, page in enumerate(pages, 1))
    else:
        pages = tuple(f"{page}\n{number} / 2" for number, page in enumerate(pages, 1))
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": pages}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.150000")


def test_entitlement_does_not_copy_ex_reference_or_after_tax_amount() -> None:
    document = _document(plan="向全体股东每10股派1.002381元人民币现金（含税；扣税后每10股派0.9021429元）。",
                         reference="除权除息价格=股权登记日收盘价-0.0999693元/股。")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None
    terms, = result.review.terms
    assert terms.cash_per_share == Decimal("0.1002381")
    assert terms.reference_cash_per_share == Decimal("0.0999693")
    assert result.review.evidence_refs == (document.url + "#sha256=" + document.sha256,)


@pytest.mark.parametrize("title,expected", [
    ("2025年季度权益分派实施公告", True),
    ("2025年第一季度权益分派实施公告", False),
    ("2024年季度权益分派实施公告", False),
])
def test_generic_quarter_title_requires_same_year_and_registration_identity(title: str, expected: bool) -> None:
    source = _source().model_copy(update={"end_date": date(2025, 9, 30)})
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否").model_copy(
        update={"title": title})
    result = extract_distribution_review((source,), (document,))
    assert (result.review is not None) is expected
    if expected:
        assert result.review.record_date == source.record_date
        other = source.model_copy(update={"record_date": date(2026, 9, 8)})
        assert extract_distribution_review((other,), (document,)).review is None


def test_beijing_equity_registration_terms_keep_gross_cash_and_actual_payment_date() -> None:
    source = _source().model_copy(update={"ts_code": "920879.BJ", "symbol": "920879"})
    document = _document(plan="以公司现有总股本200,868,814股为基数，向全体股东每10股派0.800000元人民币现金（含税）。",
        reference="").model_copy(update={"symbol": "920879"})
    document = document.model_copy(update={"pages": tuple(page.replace("600000", "920879").replace(
        "股权登记日", "权益登记日") for page in document.pages)})
    result = extract_distribution_review((source,), (document,))
    assert result.review is not None
    assert result.review.terms[0].cash_per_share == Decimal("0.08")
    assert result.review.terms[0].reference_cash_per_share == Decimal("0.08")
    assert result.review.pay_date == date(2026, 9, 10)


def test_historical_code_requires_proven_alias_and_original_pdf_header() -> None:
    source = _source().model_copy(update={"symbol": "920685", "ts_code": "920685.BJ"})
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    document = document.model_copy(update={"source": "bse", "symbol": "430685", "announcement_id": "a" * 32,
        "pages": tuple(page.replace("600000", "430685") for page in document.pages)})
    assert extract_distribution_review((source,), (document,)).reason == "missing_implementation"
    alias = SecurityIdentity(symbol="920685", published_symbol="430685",
        evidence_url="https://www.bse.cn/service/code_mapping.html", evidence_sha256="2" * 64)
    review = extract_distribution_review((source,), (document,), (alias,)).review
    assert review is not None
    assert review.action_id == f"bse:{'a' * 32}:ordinary_shareholder"
    assert review.evidence_refs[-1] == alias.evidence_url + "#sha256=" + alias.evidence_sha256
    incorrect = document.model_copy(update={"pages": tuple(page.replace("430685", "920685") for page in document.pages)})
    result = extract_distribution_review((source,), (incorrect,), (alias,))
    assert result.review is None
    assert result.reason.startswith("pdf_security_identity_mismatch")


def test_neeq_gross_cash_template_excludes_deducted_qfii_payment() -> None:
    document = _document(plan="以公司现有总股本66,590,000股为基数，向全体股东每10股派2.80元人民币现金"
        "（个人股东、投资基金适用股息红利差别化个人所得税政策；QFII实际每10股派2.52元）。"
        "持股1个月以内每10股补缴税款0.56元。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None
    assert result.review.terms[0].cash_per_share == Decimal("0.28")
    assert result.review.terms[0].reference_cash_per_share == Decimal("0.28")
    unknown = document.model_copy(update={"pages": (document.pages[0].replace(
        "个人股东、投资基金适用股息红利差别化个人所得税政策", "未说明税务口径"),)})
    assert extract_distribution_review((_source(),), (unknown,)).review is None


def test_separate_revision_notice_cannot_be_ignored_by_title_variant() -> None:
    original = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    amendment = original.model_copy(update={"announcement_id": "2", "title": "关于权益分派方案修订的公告",
        "published_date": date(2026, 9, 2), "pages": ("关于权益分派方案修订的公告\n" + original.pages[0],)})
    result = extract_distribution_review((_source(),), (original, amendment))
    assert result.review is None
    assert result.reason == "separate_revision_requires_review:2"


@pytest.mark.parametrize("title,requires_review", [
    ("2026年半年度权益分派实施公告的更正公告", True),
    ("关于公司2026年半年度权益分派实施公告的更正公告", True),
    ("2025年半年度权益分派实施公告的更正公告", False),
    ("2026年年度权益分派实施公告的更正公告", False),
    ("2026年半年度报告的更正公告", False),
])
def test_explicit_event_correction_need_not_repeat_registration_date(title: str, requires_review: bool) -> None:
    original = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    correction = original.model_copy(update={"announcement_id": "2", "title": title,
        "published_date": date(2026, 9, 2),
        "pages": ("权益分派原现金红利将于2026年9月11日到账，现更正为2026年9月10日到账。",)})
    result = extract_distribution_review((_source(),), (original, correction))
    assert (result.review is None) is requires_review
    if requires_review:
        assert result.reason == "separate_revision_requires_review:2"


@pytest.mark.parametrize("mentions_registration", [False, True])
def test_unrelated_invalid_dates_do_not_crash_event_matching(mentions_registration: bool) -> None:
    original = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    text = "报告书中的人员经历为2017/11/31。权益分派情况。"
    if mentions_registration:
        text += "本次股权登记日为2026年9月9日。"
    revision = original.model_copy(update={"announcement_id": "2", "title": "重组报告书（修订稿）",
        "published_date": date(2026, 9, 2), "pages": (text,)})
    result = extract_distribution_review((_source(),), (original, revision))
    assert (result.review is None) is mentions_registration
    if mentions_registration:
        assert result.reason == "separate_revision_requires_review:2"


def test_stock_reference_is_independent_of_received_shares() -> None:
    document = _document(plan="向全体股东每10股转增4股，不派发现金红利。",
                         reference="除权除息价格=股权登记日收盘价/（1+0.3901769）。")
    review = extract_distribution_review((_source(),), (document,)).review
    assert review is not None
    assert review.pay_date is None
    assert review.div_listdate == date(2026, 9, 10)
    assert review.terms[0].shares_per_share == Decimal("0.4")
    assert review.terms[0].reference_shares_per_share == Decimal("0.3901769")


@pytest.mark.parametrize("formula", [
    "除权除息价格=（股权登记日收盘价-0.2483639）÷（1+0.2980367）。",
    "除权（息）参考价格=（前收盘价格−0.2483639）/（1+0.2980367）。",
    "除权（息）参考价格=（前收盘价格-现金红利）÷（1+流通股份变动比例）"
    "=（前收盘价格-0.2483639）÷（1+0.2980367）。",
])
def test_cash_and_stock_reference_come_from_the_same_explicit_formula(formula: str) -> None:
    document = _document(plan="每10股派2.5元人民币现金（含税），同时每10股转增3股。", reference=formula)
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    terms, = result.review.terms
    assert terms.cash_per_share == Decimal("0.25")
    assert terms.shares_per_share == Decimal("0.3")
    assert terms.reference_cash_per_share == Decimal("0.2483639")
    assert terms.reference_shares_per_share == Decimal("0.2980367")


@pytest.mark.parametrize("reference", [
    "除权除息价格=（股权登记日收盘价-0.24）/（1+0.29）。"
    "另：除权除息价格=（股权登记日收盘价-0.24）/（1+0.28）。",
    "除权除息价格=（股权登记日收盘价-0.24）/（1+0.29）。"
    "另：除权除息价格=股权登记日收盘价-0.23元。",
    "其他交易价格=（股权登记日收盘价-0.24）/（1+0.29）。",
    "除权除息价格按交易所公布。另：购买价格=（股权登记日收盘价-0.24）/（1+0.29）。",
])
def test_conflicting_or_unlabelled_paired_formula_remains_unresolved(reference: str) -> None:
    document = _document(plan="每10股派2.5元人民币现金（含税），同时每10股转增3股。", reference=reference)
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("ex_reference_not_confirmed")


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


@pytest.mark.parametrize("cash_first", [False, True])
@pytest.mark.parametrize("short_labels", [False, True])
def test_table_keeps_cash_payment_and_new_share_listing_dates_separate(cash_first: bool, short_labels: bool) -> None:
    fields = ["现金红利发放日", "新增无限售条件流通股份上市日"] if cash_first else [
        "新增无限售条件流通股份上市日", "现金红利发放日"]
    dates = ["2026/9/10", "2026/9/11"] if cash_first else ["2026/9/11", "2026/9/10"]
    document = _document(plan="每股派发现金红利0.26元（含税），每股转增0.4股。",
                         reference="差异化分红送转：否")
    document = document.model_copy(update={"pages": (f"""证券代码：600000
登记在册的本公司全体股东。
二、分配方案
每股派发现金红利0.26元（含税），每股转增0.4股。
三、相关日期
股份类别 股权登记日 最后交易日 除权（息）日 {' '.join(fields)}
A股 2026/9/9 - 2026/9/10 {' '.join(dates)}
差异化分红送转：否""",)})
    if short_labels:
        text = document.pages[0].replace("除权（息）日", "除权除息日").replace(
            "新增无限售条件流通股份上市日", "新增可流通股份上市日").replace("现金红利发放日", "红利发放日")
        document = document.model_copy(update={"pages": (text,)})
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.pay_date == date(2026, 9, 10)
    assert result.review.div_listdate == date(2026, 9, 11)


@pytest.mark.parametrize("condition", ["", "条件"])
@pytest.mark.parametrize("separator", ["", "为", "："])
def test_stock_listing_date_accepts_explicit_unrestricted_trading_start(condition: str, separator: str) -> None:
    document = _document(plan="每10股派10元人民币现金（含税），每10股转增6股。",
        reference="差异化分红送转：否")
    document = document.model_copy(update={"pages": (document.pages[0].replace(
        "新增可流通股份上市日：2026年9月10日",
        f"五、本次所送（转）的无限售{condition}流通股的起始交易日{separator}2026年9月11日"),)})
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.pay_date == date(2026, 9, 10)
    assert result.review.div_listdate == date(2026, 9, 11)


@pytest.mark.parametrize("label", ["现金股利将于", "现金股利于", "红利发放日为：", "现金红利发放日："])
def test_explicit_cash_payment_wording_preserves_later_payment(label: str) -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    text = document.pages[0].replace("现金红利将于2026年9月10日", f"{label}2026年9月11日")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.ex_date == date(2026, 9, 10)
    assert result.review.pay_date == date(2026, 9, 11)


@pytest.mark.parametrize("label", ["现金股利将于", "红利发放日为："])
def test_conflicting_explicit_cash_payment_wording_remains_unresolved(label: str) -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    text = document.pages[0] + f"{label}2026年9月11日。"
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is None
    assert result.reason.startswith("settlement_date_ambiguous")


@pytest.mark.parametrize("label", ["新增可流通股上市日为：", "新增股份上市日为："])
def test_explicit_new_share_listing_wording_preserves_separate_settlement(label: str) -> None:
    document = _document(plan="每10股派1元人民币现金（含税），每10股转增4股。", reference="差异化分红送转：否")
    text = document.pages[0].replace("新增可流通股份上市日：2026年9月10日", f"{label}2026年9月11日")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.pay_date == date(2026, 9, 10)
    assert result.review.div_listdate == date(2026, 9, 11)


@pytest.mark.parametrize("has_conflict", [False, True])
def test_joint_listing_and_payment_label_requires_consistent_payment(has_conflict: bool) -> None:
    document = _document(plan="每10股派1元人民币现金（含税），每10股转增4股。", reference="差异化分红送转：否")
    text = document.pages[0].replace("新增可流通股份上市日：2026年9月10日",
        "新增可流通股份上市日（红利发放日）为：2026年9月11日")
    if not has_conflict:
        text = text.replace("现金红利将于2026年9月10日发放。", "")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    if has_conflict:
        assert result.review is None
        assert result.reason.startswith("settlement_date_ambiguous")
    else:
        assert result.review is not None, result.reason
        assert result.review.pay_date == result.review.div_listdate == date(2026, 9, 11)
        assert result.review.ex_date == date(2026, 9, 10)


def test_share_credit_date_cannot_supply_missing_listing_date() -> None:
    document = _document(plan="每10股派1元人民币现金（含税），每10股转增4股。", reference="差异化分红送转：否")
    text = document.pages[0].replace("新增可流通股份上市日：2026年9月10日。",
        "本次所转增股份于2026年9月10日直接记入股东证券账户。")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is None
    assert result.reason.startswith("settlement_date_ambiguous")


@pytest.mark.parametrize("cash,label", [(True, "现金红利发放日"), (False, "新增可流通股上市日"),
    (False, "本次所送（转）的无限售条件流通股的起始交易日")])
def test_settlement_table_header_cannot_consume_first_registration_cell(cash: bool, label: str) -> None:
    plan = "每10股派1元人民币现金（含税）。" if cash else "每10股转增4股，不派发现金红利。"
    document = _document(plan=plan, reference="差异化分红送转：否")
    text = f"""证券代码：600000
二、分配方案
{plan}
三、相关日期
股权登记日 除权（息）日 {label}
2026/9/9 2026/9/10 2026/9/11
登记在册的本公司全体股东。
差异化分红送转：否"""
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.record_date == date(2026, 9, 9)
    assert result.review.ex_date == date(2026, 9, 10)
    assert result.review.pay_date == (date(2026, 9, 11) if cash else None)
    assert result.review.div_listdate == (None if cash else date(2026, 9, 11))


def test_exact_pdf_page_disclaimer_does_not_break_registered_shareholder_scope() -> None:
    document = _document(plan="每10股派4元人民币现金（含税），每10股转增8股。",
        reference="差异化分红送转：否")
    disclaimer = "本公司及董事会全体成员保证信息披露的内容真实、准确、完整，没有虚假记载、误导性陈述或重大遗漏。"
    document = document.model_copy(update={"pages": (document.pages[0].replace(
        "登记在册", "登记在\n" + disclaimer + "\n册"),)})
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].shares_per_share == Decimal("0.8")


@pytest.mark.parametrize("plan,cash,shares", [
    ("以公司现有总股本431,021,374股为基数，向全体股东每10股送红股3股，派0.8元人民币现金（含税）。", "0.08", "0.3"),
    ("每10股派发人民币现金红利1.000000元（含税；扣税后每10股派0.900000元）。", "0.1", "0"),
])
def test_explicit_gross_cash_keeps_shared_per_ten_share_unit(plan: str, cash: str, shares: str) -> None:
    review = extract_distribution_review((_source(),), (_document(plan=plan, reference="差异化分红送转：否"),)).review
    assert review is not None
    assert review.terms[0].cash_per_share == Decimal(cash)
    assert review.terms[0].shares_per_share == Decimal(shares)


def test_explicit_latest_capital_plan_does_not_use_superseded_proposal_amount() -> None:
    plan = ("以公司2017年12月31日总股本241,725,681股为基数，向全体股东每10股派5元人民币现金（含税）。"
        "由于回购注销，按最新股本计算的2017年年度权益分派方案为：以公司现有总股本241,215,681股为基数，"
        "向全体股东每10股派5.010571元人民币现金（含税；扣税后每10股派4.509514元）。")
    review = extract_distribution_review((_source(),), (_document(plan=plan, reference=""),)).review
    assert review is not None
    assert review.terms[0].cash_per_share == Decimal("0.5010571")
    assert review.terms[0].reference_cash_per_share == Decimal("0.5010571")


@pytest.mark.parametrize("boundary", ["2026", "2026年9"])
def test_consecutive_page_labels_cannot_enter_cross_page_dates(boundary: str) -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    text = document.pages[0]
    offset = text.index(boundary, text.index("除权除息日")) + len(boundary)
    pages = ("1\n" + text[:offset], "2\n" + text[offset:])
    document = document.model_copy(update={"pages": pages})
    review = extract_distribution_review((_source(),), (document,)).review
    assert review is not None
    assert review.ex_date == date(2026, 9, 10)


def test_unconfirmed_edge_number_and_body_amount_are_preserved() -> None:
    document = _document(plan="每10股派\n1\n元人民币现金（含税）。", reference="差异化分红送转：否")
    first, second = document.pages[0].split("2026年9月10日", maxsplit=1)
    document = document.model_copy(update={"pages": (first + "2026年9", "2\n月10日" + second)})
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("registration_or_ex_date_ambiguous")


@pytest.mark.parametrize("plan,cash", [
    ("以公司现有股份总额1,386,564,967股为基数，向全体股东每10股派发现金红利1.15元（含税）。", "0.115"),
    ("按2017年末总股本671,080,800股为基数每10股派发现金股利6.80元（含税），不送红股。", "0.68"),
])
def test_explicit_full_capital_base_is_independent_of_shareholder_word_order(plan: str, cash: str) -> None:
    review = extract_distribution_review((_source(),), (_document(plan=plan, reference=""),)).review
    assert review is not None
    assert review.terms[0].reference_cash_per_share == Decimal(cash)


def test_excluded_restricted_shares_prevent_assuming_uniform_ex_reference() -> None:
    document = _document(plan="以公司总股本100,000股为基数，向全体股东每10股派1元人民币现金（含税）。",
        reference="已离职激励对象所持有限制性股票不参与本次分红。")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is None
    assert result.reason.startswith("ex_reference_not_confirmed")


def test_page_disclaimer_variant_is_not_part_of_the_shareholder_scope() -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    disclaimer = "本公司及董事会全体成员保证信息披露内容的真实、准确和完整，没有虚假记载、误导性陈述或重大遗漏。"
    document = document.model_copy(update={"pages": (document.pages[0].replace("本公司全体股东", "本公司" + disclaimer + "全体股东"),)})
    assert extract_distribution_review((_source(),), (document,)).review is not None


def test_capital_reserve_distribution_is_not_misread_as_zero_shares() -> None:
    document = _document(plan="以公司现有股本80,000,000股为基数，向全体股东每10股派发现金股利人民币2.50元（含税）；"
        "同时，向全体股东每10股以资本公积转增5股。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    terms, = result.review.terms
    assert terms.cash_per_share == Decimal("0.25")
    assert terms.shares_per_share == Decimal("0.5")
    assert terms.reference_shares_per_share == Decimal("0.5")


def test_repeated_glyph_security_header_does_not_change_financial_numbers() -> None:
    document = _document(plan="每10股派11元人民币现金（含税）。", reference="差异化分红送转：否")
    text = document.pages[0].replace("证券代码：600000", "证证券券代代码码：：330000227700")
    source = _source().model_copy(update={"ts_code": "300270.SZ", "symbol": "300270"})
    document = document.model_copy(update={"symbol": "300270", "pages": (text,)})
    result = extract_distribution_review((source,), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("1.1")
    wrong = document.model_copy(update={"pages": (text.replace("330000227700", "330000227722"),)})
    assert extract_distribution_review((source,), (wrong,)).reason.startswith("pdf_security_identity_mismatch")


@pytest.mark.parametrize("wording", ["股息将于", "现金红利于", "本次红利委托中国结算深圳分公司将于"])
def test_explicit_payment_wording_preserves_actual_date(wording: str) -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    document = document.model_copy(update={"pages": (document.pages[0].replace("现金红利将于", wording),)})
    review = extract_distribution_review((_source(),), (document,)).review
    assert review is not None
    assert review.pay_date == date(2026, 9, 10)


@pytest.mark.parametrize("plan,cash", [
    ("每股A股派发现金红利人民币0.75元（含税）。", "0.75"),
    ("每10股派1.00元人民币现金。（含税；扣税后每10股派0.90元）。", "0.1"),
])
def test_explicit_a_share_and_punctuated_gross_amount(plan: str, cash: str) -> None:
    review = extract_distribution_review((_source(),), (_document(plan=plan, reference="差异化分红送转：否"),)).review
    assert review is not None
    assert review.terms[0].cash_per_share == Decimal(cash)


@pytest.mark.parametrize("disclaimer", [
    "本公司及董事会全体成员保证公告内容真实、准确和完整，没有虚假记载、误导性陈述或重大遗漏。",
    "本公司及董事会全体成员保证信息披露的内容真实、准确、完整，没有虚假记载、误导性陈述或重大遗漏。",
    "本公司及监事会全体成员保证信息披露的内容真实、准确、完整，没有虚假记载、误导性陈述或重大遗漏。",
])
def test_exact_disclaimer_cannot_split_ex_date(disclaimer: str) -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    document = document.model_copy(update={"pages": (document.pages[0].replace(
        "除权除息日为：2026年9月10日", "除权除息日为：2026年9" + disclaimer + "月10日"),)})
    review = extract_distribution_review((_source(),), (document,)).review
    assert review is not None
    assert review.ex_date == date(2026, 9, 10)


def test_repeated_identical_security_header_is_not_part_of_shareholder_scope() -> None:
    document = _document(plan="每10股派1元人民币现金（含税）。", reference="差异化分红送转：否")
    header = "证券代码：600000 证券简称：测试股份 公告编号：2026-030"
    text = document.pages[0].replace("证券代码：600000", header)
    first, second = text.split("全体股东", maxsplit=1)
    document = document.model_copy(update={"pages": (first + "全体", header + "\n股东" + second)})
    review = extract_distribution_review((_source(),), (document,)).review
    assert review is not None
    assert review.record_date == date(2026, 9, 9)


@pytest.mark.parametrize("page_labels", [("1", "2"), ("1 / 2", "2 / 2")])
def test_page_label_below_security_header_cannot_extend_cash_decimal(page_labels: tuple[str, str]) -> None:
    document = _document(plan="每10股派0.25元人民币现金（含税）。", reference="差异化分红送转：否")
    header = "证券代码：600000 证券简称：测试股份 公告编号：2026-030"
    text = document.pages[0].replace("证券代码：600000\n", "")
    first, second = text.split("0.25", maxsplit=1)
    pages = (f"{header}\n{page_labels[0]}\n{first}0.25", f"{header}\n{page_labels[1]}\n{second}")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": pages}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.025")
    assert result.review.terms[0].reference_cash_per_share == Decimal("0.025")


def test_joined_a_b_table_rows_preserve_a_share_dates() -> None:
    document = _document(plan="每股派发现金红利0.012元（含税）。", reference="差异化分红送转：否")
    document = document.model_copy(update={"pages": ("""证券代码：600000
登记在册的本公司全体股东。
二、分配方案
每股派发现金红利0.012元（含税）。
三、相关日期
股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日
Ａ股 2026/9/9 － 2026/9/10 2026/9/10 Ｂ股 2026/9/12 2026/9/9 2026/9/15
差异化分红送转：否""",)})
    review = extract_distribution_review((_source(),), (document,)).review
    assert review is not None
    assert review.record_date == date(2026, 9, 9)
    assert review.ex_date == date(2026, 9, 10)
    assert review.pay_date == date(2026, 9, 10)


def test_bonus_share_wording_keeps_listing_separate_from_cash_payment() -> None:
    document = _document(plan="", reference="").model_copy(update={"pages": ("""证券代码：600000
登记在册的本公司全体股东。
二、分配方案
每股派发现金红利0.22元（含税），每股派送红股0.2股。
三、相关日期
股份类别 股权登记日 最后交易日 除权（息）日
新增无限售条件流通股份上市日 现金红利发放日
Ａ股 2026/9/9 － 2026/9/10 2026/9/11 2026/9/10
差异化分红送转：否""",)})
    review = extract_distribution_review((_source(),), (document,)).review
    assert review is not None
    assert review.record_date == date(2026, 9, 9)
    assert review.ex_date == date(2026, 9, 10)
    assert review.pay_date == date(2026, 9, 10)
    assert review.div_listdate == date(2026, 9, 11)
    assert review.terms[0].cash_per_share == Decimal("0.22")
    assert review.terms[0].shares_per_share == Decimal("0.2")


def test_security_number_header_requires_the_exact_issuer() -> None:
    document = _document(plan="以公司总股本313457493股为基数，每10股派0.70元人民币现金（含税）。", reference="")
    text = document.pages[0].replace("证券代码", "证券号码")
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": (text,)}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.07")
    wrong = document.model_copy(update={"pages": (text.replace("证券号码：600000", "证券号码：600001"),)})
    assert extract_distribution_review((_source(),), (wrong,)).reason.startswith("pdf_security_identity_mismatch")


def test_standalone_security_footer_cannot_hide_a_share_distribution() -> None:
    document = _document(plan="以公司现有总股本120000000股为基数，每10股派10元人民币现金（含税），"
        "同时以资本公积金向全体股东每10股转增3股。", reference="")
    text = document.pages[0].replace("证券代码：600000\n", "")
    first, last = text.split("每10股转增", maxsplit=1)
    pages = (first + "每\n证券代码：600000 证券简称：测试股份 公告编号：2020-029", "10股转增" + last)
    result = extract_distribution_review((_source(),), (document.model_copy(update={"pages": pages}),))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("1")
    assert result.review.terms[0].shares_per_share == Decimal("0.3")
    assert result.review.terms[0].reference_shares_per_share == Decimal("0.3")
    assert result.review.div_listdate == date(2026, 9, 10)


@pytest.mark.parametrize("transition", ["分派变更如下：", "因此，公司应"])
def test_explicit_revised_base_uses_the_new_distribution(transition: str) -> None:
    document = _document(plan="以2017年末公司总股本986105137股为基数，每10股派发现金0.2元人民币（含税）。"
        f"{transition}以公司现有总股本1182212982股为基数，每10股派0.166823元人民币现金（含税）。", reference="")
    result = extract_distribution_review((_source(),), (document,))
    assert result.review is not None, result.reason
    assert result.review.terms[0].cash_per_share == Decimal("0.0166823")
    assert result.review.terms[0].reference_cash_per_share == Decimal("0.0166823")
    unreadable = document.model_copy(update={"pages": (document.pages[0].replace("0.166823", "尚待确定"),)})
    result = extract_distribution_review((_source(),), (unreadable,))
    assert result.review is None
    assert result.reason.startswith("distribution_amount_missing")
