# filepath: src/data_system/distribution_announcements.py
"""Extract ordinary-shareholder distributions from identified implementation PDFs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import PurePosixPath
import re
from typing import NamedTuple, Self
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.data_system.corporate_actions import CorporateActionReview, DividendRecord, DistributionTerms


class AnnouncementText(BaseModel):
    """An identified PDF and its extracted pages; publication comes from the index.

    Example:
        document = AnnouncementText(announcement_id="1225575286", symbol="688808",
            title="2026年半年度权益分派实施公告", published_date="2026-09-22",
            url="https://static.cninfo.com.cn/finalpage/2026-09-22/1225575286.PDF",
            sha256="0" * 64, pages=("证券代码：688808",))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    announcement_id: str = Field(pattern=r"^[0-9]+$")
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    title: str
    published_date: date
    url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    pages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DistributionExtraction:
    """A complete review or an explicit unresolved extraction reason.

    Example:
        extraction = DistributionExtraction(review=None, reason="missing_implementation")
        assert extraction.review is None
    """

    review: CorporateActionReview | None
    reason: str


class ReviewExtractionManifest(BaseModel):
    """Bind produced reviews and unresolved reasons to exact stored inputs.

    Example:
        manifest = ReviewExtractionManifest.model_validate_json(path.read_bytes())
        assert manifest.reviews_sha256 == sha256(reviews_path.read_bytes()).hexdigest()
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    inputs: dict[str, str]
    producer_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviews_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    issues: dict[str, str]

    @model_validator(mode="after")
    def _validate_input_paths(self) -> Self:
        for relative, digest in self.inputs.items():
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError(f"invalid extraction input identity: {relative}")
        return self


_DATE = r"(20\d{2})[年/.-](1[012]|0?[1-9])[月/.-](3[01]|[12][0-9]|0?[1-9])(?=日|20\d{2}|[^0-9]|$)日?"
_CASH = (
    r"每(10|一)?股(?:派发|派送|分派|派|发放|分配)?(?:现金红利|现金股利|现金股息|股息|现金)?"
    r"(?:为|人民币|人民币现金)?([0-9]+(?:\.[0-9]+)?)元(?:人民币)?(?:现金)?\(含税",
    r"每(10|一)?股(?:派发|派送|派|发放|分配)?(?:人民币|人民币现金)([0-9]+(?:\.[0-9]+)?)元(?:现金)?\(含税",
)
_SHARES = r"每(10)?股(?:转增|送红股|送)([0-9]+(?:\.[0-9]+)?)股"


class _ImplementationTerms(NamedTuple):
    record_date: date
    ex_date: date
    pay_date: date | None
    listing_date: date | None
    terms: DistributionTerms


class _ExReference(NamedTuple):
    cash_per_share: Decimal
    shares_per_share: Decimal


def normalize_announcement_text(text: str) -> str:
    """Normalize typography without modifying financial numbers.

    Example:
        assert normalize_announcement_text("每１０股 １元") == "每10股1元"
    """
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _dates(pattern: str, text: str) -> set[date]:
    return {date(int(year), int(month), int(day)) for year, month, day in re.findall(pattern + _DATE, text)}


def _per_share(pattern: str, text: str) -> set[Decimal]:
    return {Decimal(amount) / (10 if unit == "10" else 1) for unit, amount in re.findall(pattern, text)}


def _reference_terms(text: str, plan: str, cash: Decimal, shares: Decimal) -> _ExReference | None:
    # The equal-rate case requires an affirmative statement about the distribution base.
    uniform = bool(re.search(r"差异化分红送转:否", text) or re.search(
        r"总股本(?:[0-9,.]+股|为?[0-9,.]+股)?为基数[,、:]?(?:以资本公积金)?向全体(?:在册)?股东", plan,
    ) or re.search(r"总股本剔除已回购股份0股后的[0-9,]+股为基数", plan))
    deductions: set[Decimal] = set()
    for match in re.finditer(r"除权(?:除息|\(息\))?(?:参考)?(?:价格|价)", text):
        excerpt = text[match.start():match.end() + 400]
        deductions.update(Decimal(value) for value in re.findall(
            r"(?:前收盘价格?|登记日收盘价格?|前一(?:交易)?日收盘价)[−－—‒-](?:人民币)?([0-9]+(?:\.[0-9]+)?)", excerpt,
        ))
        deductions.update(Decimal(value) for value in re.findall(
            r"每股现金红利(?:\(|应以(?:人民币)?)?([0-9]+(?:\.[0-9]+)?)元", excerpt,
        ))
    deductions.update(Decimal(value) for value in re.findall(
        r"按A股除权前总股本[^。]{0,60}计算的每股派息\(含税\):([0-9]+(?:\.[0-9]+)?)元", text,
    ))
    if len(deductions) == 1 and shares == 0:
        return _ExReference(deductions.pop(), Decimal(0))
    share_references = {Decimal(value) for value in re.findall(r"收盘价/\(1\+([0-9]+(?:\.[0-9]+)?)\)", text)}
    if len(share_references) == 1 and cash == 0 and shares > 0:
        return _ExReference(Decimal(0), share_references.pop())
    if not deductions and uniform and not re.search(r"差异化分红送转:是|回购[^。]{0,60}不参与", text):
        return _ExReference(cash, shares)
    return None


def _document_terms(document: AnnouncementText) -> _ImplementationTerms | str:
    text = normalize_announcement_text("\n".join(document.pages))
    if document.symbol not in text[:650]:
        return "pdf_security_identity_mismatch"
    if re.search(r"重整|(?<!转)债转股|偿债|以股抵债", text):
        return "special_beneficiary_requires_review"
    if not re.search(r"(?:登记在册|登记在)[^。]{0,80}全体[^。]{0,10}股东", text):
        return "shareholder_scope_unclear"
    sections = re.split(r"二[、.]\s*(?:本次实施的)?(?:权益分派方案|分配方案|利润分配方案|资本公积金转增股本方案)", text)
    plan = re.split(r"三[、.]", sections[-1], maxsplit=1)[0] if len(sections) == 2 else text
    cash_values = set().union(*(_per_share(pattern, plan) for pattern in _CASH))
    share_values = _per_share(_SHARES, plan)
    if len(cash_values) > 1 or len(share_values) > 1:
        return "distribution_amount_ambiguous"
    if not cash_values and not share_values:
        return "distribution_amount_missing"
    if not cash_values and not re.search(r"不(?:派发|进行|派送)现金(?:红利|分红)|不分配现金", plan):
        return "zero_cash_not_confirmed"
    cash = next(iter(cash_values), Decimal(0))
    shares = next(iter(share_values), Decimal(0))
    records = _dates(r"股权登记日期?(?:为|即|:){0,3}", text)
    ex_dates = _dates(r"除(?:权除息日(?:\(红利发放日\))?|权日\(除息日\)|权\(息\)日|息日|权日)(?:为|:){0,3}", text)
    pay_dates = _dates(r"现金(?:红利|分红)将于", text)
    listing_dates = _dates(r"(?:新增可流通股份上市日|无限售条件流通股的起始交易日)(?:为|:){0,3}", text)
    for page in document.pages:
        for line in page.splitlines():
            line = normalize_announcement_text(line)
            if re.match(r"(?:A股|20\d{2}[/年])", line):
                dates = [date(int(y), int(m), int(d)) for y, m, d in re.findall(_DATE, line)]
                if len(dates) == 3 and "B股" not in line:
                    records.add(dates[0])
                    ex_dates.add(dates[1])
                    if cash:
                        pay_dates.add(dates[2])
                    if shares:
                        listing_dates.add(dates[2])
    if len(records) != 1 or len(ex_dates) != 1:
        return "registration_or_ex_date_ambiguous"
    if (cash and len(pay_dates) != 1) or (shares and len(listing_dates) != 1):
        return "settlement_date_ambiguous"
    reference = _reference_terms(text, plan, cash, shares)
    if reference is None:
        return "ex_reference_not_confirmed"
    terms = DistributionTerms(published_date=document.published_date, cash_per_share=cash,
        shares_per_share=shares, reference_cash_per_share=reference.cash_per_share,
        reference_shares_per_share=reference.shares_per_share)
    return _ImplementationTerms(records.pop(), ex_dates.pop(), pay_dates.pop() if cash else None,
                                listing_dates.pop() if shares else None, terms)


def extract_distribution_review(
    records: Sequence[DividendRecord], documents: Sequence[AnnouncementText],
) -> DistributionExtraction:
    """Resolve one source candidate only when all matching implementation versions agree.

    Example:
        extracted = extract_distribution_review(records=source_group, documents=pdfs)
        if extracted.review is None:
            print(extracted.reason)
    """
    source = records[0]
    year, month = source.end_date.year, source.end_date.month
    period = {3: "第一季度|一季度", 6: "上?半年度|(?:度)?(?:第[一二三四]次)?中期", 9: "前三季度|第三季度", 12: "年度"}.get(month)
    if period is None:
        return DistributionExtraction(None, "nonstandard_reporting_period")
    matching = []
    for document in documents:
        title = normalize_announcement_text(document.title)
        if document.symbol != source.symbol or not re.search(rf"{year}年?(?:{period})", title):
            continue
        if not re.search(r"(?:权益分[派配]|利润分[配派]|分红派息|现金分红|转增股本).*实施(?:公告|的公告)", document.title):
            continue
        if any(word in document.title for word in ("回购", "转股价格", "转债", "律师", "核查", "结果")):
            continue
        if "更正公告" in document.title and "更正后" not in document.title:
            continue
        text = normalize_announcement_text("\n".join(document.pages))
        if source.record_date not in _dates("", text):
            continue
        matching.append(document)
    if not matching:
        return DistributionExtraction(None, "missing_implementation")
    matching.sort(key=lambda document: (document.published_date, document.announcement_id))
    for document in documents:
        if (document.symbol != source.symbol or document in matching
            or document.published_date < matching[0].published_date
            or not re.search(r"更正|补充|调整|取消|终止", document.title)
            or re.search(r"(?:回购(?:股份)?|转股|发行|授予|行权)价(?:格)?", document.title)):
            continue
        text = normalize_announcement_text("\n".join(document.pages))
        if source.record_date in _dates("", text) and re.search(r"权益分派|利润分配|分红派息|转增股本", text):
            return DistributionExtraction(None, f"separate_revision_requires_review:{document.announcement_id}")
    versions: list[DistributionTerms] = []
    phase_dates = None
    for document in matching:
        parsed = _document_terms(document)
        if isinstance(parsed, str):
            return DistributionExtraction(None, f"{parsed}:{document.announcement_id}")
        record_date, ex_date, pay_date, listing_date, terms = parsed
        if record_date != source.record_date:
            return DistributionExtraction(None, "announcement_registration_mismatch")
        dates = (record_date, ex_date, pay_date, listing_date)
        if phase_dates is not None and dates != phase_dates:
            return DistributionExtraction(None, "phase_date_revision_requires_review")
        phase_dates = dates
        if versions:
            prior = versions[-1]
            if terms.model_dump(exclude={"published_date"}) == prior.model_dump(exclude={"published_date"}):
                continue
            if terms.published_date == prior.published_date:
                return DistributionExtraction(None, "same_day_revision_order_unknown")
        versions.append(terms)
    source_digest = sha256("\n".join(sorted(row.source_row_id for row in records)).encode()).hexdigest()
    try:
        review = CorporateActionReview(candidate_id=source.candidate_id, source_digest=source_digest,
            action_id=f"cninfo:{matching[0].announcement_id}:ordinary_shareholder", shareholder_scope="ordinary_shareholder",
            record_date=record_date, ex_date=ex_date, pay_date=pay_date, div_listdate=listing_date, terms=tuple(versions),
            evidence_refs=tuple(f"{document.url}#sha256={document.sha256}" for document in matching),
            reason="implementation_pdf_terms_and_reference_confirmed")
    except ValidationError as error:
        return DistributionExtraction(None, f"unsupported_announcement_timeline:{error.errors(include_url=False)}")
    return DistributionExtraction(review, "implementation_pdf_terms_and_reference_confirmed")
