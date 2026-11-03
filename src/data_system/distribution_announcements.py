# filepath: src/data_system/distribution_announcements.py
"""Extract ordinary-shareholder distributions from identified implementation PDFs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import PurePosixPath
import re
from typing import Literal, NamedTuple, Self
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from src.data_system.announcement_evidence import SecurityIdentity
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord, DistributionTerms


class AnnouncementText(BaseModel):
    """An identified PDF and its extracted pages; publication comes from the index.

    Example:
        document = AnnouncementText(source="cninfo", announcement_id="1225575286", symbol="688808",
            title="2026年半年度权益分派实施公告", published_date="2026-09-22",
            url="https://static.cninfo.com.cn/finalpage/2026-09-22/1225575286.PDF",
            pages=("证券代码：688808",))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["cninfo", "bse"]
    announcement_id: str = Field(pattern=r"^(?:[a-f0-9]+|manual[0-9]+)$")
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    title: str
    published_date: date
    published_ts_us: StrictInt | None = None
    url: str
    pages: tuple[str, ...]

    @property
    def normalized_text(self) -> str:
        """Join PDF body text after removing a consistent sequence of standalone page labels.

        Example:
            text = document.normalized_text
            assert "股权登记日" in text
        """
        lines = [page.strip().splitlines() for page in self.pages]
        if lines and lines[0]:
            header = normalize_announcement_text(lines[0][0])
            if header.startswith("证券代码") and "证券简称" in header and "公告编号" in header:
                if len(lines) > 1 and all(len(page) > 1 and normalize_announcement_text(page[0]) == header
                    and normalize_announcement_text(page[1]) in (str(number), f"{number}/{len(lines)}")
                    for number, page in enumerate(lines, 1)):
                    lines = [[page[0], *page[2:]] for page in lines]
                lines = [lines[0], *(page[1:] if page and normalize_announcement_text(page[0]) == header else page
                    for page in lines[1:])]
            elif len(lines) > 1 and (header.endswith("股份有限公司") or re.fullmatch(r"[^0-9]+20\d{2}年公告", header)):
                if all(page and normalize_announcement_text(page[0]) == header for page in lines):
                    lines = [page[1:] for page in lines]
        if len(lines) > 1 and all(page and normalize_announcement_text(page[0]) in (str(number), f"{number}/{len(lines)}")
                                 for number, page in enumerate(lines, 1)):
            if re.match(r"(?:证券|股票|A股)代码", normalize_announcement_text("\n".join(lines[0][1:]))):
                lines = [page[1:] for page in lines]
        elif len(lines) > 1 and all(page and normalize_announcement_text(page[-1]) in (str(number), f"{number}/{len(lines)}")
                                   for number, page in enumerate(lines, 1)):
            if re.match(r"(?:证券|股票|A股)代码", normalize_announcement_text("\n".join(lines[0]))):
                lines = [page[:-1] for page in lines]
        for number, page in enumerate(lines):
            if page and re.fullmatch(rf"证券代码:{self.symbol}证券简称:[^:。]{{1,30}}公告编号:20\d{{2}}-[0-9]+",
                                     normalize_announcement_text(page[-1])):
                lines[number] = page[:-1]
        text = normalize_announcement_text("\n".join("\n".join(page) for page in lines))
        return re.sub(r"本公司及(?:董事会|监事会)全体成员保证(?:信息披露(?:的内容|内容的)|公告内容)真实、准确(?:、|和)完整,"
            r"没有虚假记载、误导性陈述或重大遗漏。", "", text)


@dataclass(frozen=True, slots=True)
class DistributionExtraction:
    """A confirmed review or a diagnosis; source usability is resolved separately.

    Example:
        extraction = DistributionExtraction(review=None, reason="missing_implementation")
        assert extraction.review is None
    """

    review: CorporateActionReview | None
    reason: str


class ReviewExtractionManifest(BaseModel):
    """Record extraction inputs and diagnoses, including nonblocking source conflicts.

    Example:
        manifest = ReviewExtractionManifest.model_validate_json(path.read_bytes())
        print(manifest.issues)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    inputs: tuple[str, ...]
    issues: dict[str, str]

    @model_validator(mode="after")
    def _validate_input_paths(self) -> Self:
        for relative in self.inputs:
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"invalid extraction input identity: {relative}")
        return self


_DATE = r"(20\d{2})[年/.-](1[012]|0?[1-9])[月/.-](3[01]|[12][0-9]|0?[1-9])(?=日|20\d{2}|[^0-9]|$)日?"
_CASH = (
    r"每(10|一)?股(?:A股)?(?:实际)?(?:送红股[0-9]+(?:\.[0-9]+)?股[,、])?(?:派发|派送|分派|派|发放|分配)?(?:20\d{2}年(?:末期|中期))?(?:现金红利|现金股利|现金股息|股息|现金)?"
    r"(?:为|人民币|人民币现金)?([0-9]+(?:\.[0-9]+)?)元(?:人民币)?(?:现金(?:红利|股利)?)?。?[\[(](?:含税|税前(?=[\]);,。]))",
    r"每(10|一)?股(?:派发|派送|派|发放|分配)?人民币(?:现金红利|现金股利|现金)?([0-9]+(?:\.[0-9]+)?)元(?:现金)?[\[(](?:含税|税前(?=[\]);,。]))",
    r"每(10|一)?股派([0-9]+(?:\.[0-9]+)?)元人民币现金\(个人股东、投资基金适用股息红利差别化个人所得税政策",
    r"调整后(?:的)?每(一)?股现金红利=[^。;]{1,240}=([0-9]+(?:\.[0-9]+)?)元/股\(含税",
    r"调整后每(一)?股现金分红(?:约)?为([0-9]+(?:\.[0-9]+)?)元\(含税",
)
_SHARES = (
    (r"(?<!资本公积金)(?<!资本公积)每(10)?股"
     r"(?:派(?:发|送)?(?:现金红利)?[0-9]+(?:\.[0-9]+)?元(?:人民币现金)?\(含税\)(?:现金红利)?[,、])?"
     r"(?:派送红股|送红股|送)([0-9]+(?:\.[0-9]+)?)股",),
    (r"每(10)?股(?:以资本公积金?)?转增([0-9]+(?:\.[0-9]+)?)股",
     r"以资本公积金?每(10)?股送([0-9]+(?:\.[0-9]+)?)股"),
)


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


def _mentions_date(text: str, expected: date) -> bool:
    return any((int(year), int(month), int(day)) == (expected.year, expected.month, expected.day)
               for year, month, day in re.findall(_DATE, text))


def _per_share(pattern: str, text: str) -> set[Decimal]:
    return {Decimal(amount) / (10 if unit == "10" else 1) for unit, amount in re.findall(pattern, text)}


def _reference_terms(text: str, plan: str, cash: Decimal, shares: Decimal) -> _ExReference | None:
    # The equal-rate case requires an affirmative statement about the distribution base.
    uniform = bool(re.search(r"差异化分红送转:否", text) or re.search(
        r"(?:总股本|股份总额|公司现有股本(?:总数|总额)?)(?:为?[0-9,.]+(?:万股|股)?)?为基(?:数|准)"
        r"(?!\((?:已)?(?:扣除|扣减|剔除|不含|不包括))", plan,
    ) or re.search(r"总股本剔除已回购股份0股后的[0-9,]+股为基数", plan))
    deductions: set[Decimal] = set()
    paired_references: set[_ExReference] = set()
    for match in re.finditer(r"除权(?:除息|\(息\))?(?:参考)?(?:价格|价)", text):
        excerpt = text[match.end():match.end() + 400]
        paired_references.update(_ExReference(Decimal(cash_value), Decimal(share_value))
            for cash_value, share_value in re.findall(
                r"^=(?:\([^。=]{1,100}\)[/÷]\([^。=]{1,100}\)=)?"
                r"\((?:前收盘价格?|股权登记日收盘价|除权前一交易日收盘价)[−－—‒-]([0-9]+(?:\.[0-9]+)?)"
                r"\)[/÷]\(1\+([0-9]+(?:\.[0-9]+)?)\)", excerpt))
        deductions.update(Decimal(value) for value in re.findall(
            r"(?:前收盘价格?|登记日收盘价格?|前一(?:交易)?日收盘价)[−－—‒-](?:人民币)?([0-9]+(?:\.[0-9]+)?)", excerpt,
        ))
        deductions.update(Decimal(value) for value in re.findall(
            r"每股现金红利(?:\(|应以(?:人民币)?)?([0-9]+(?:\.[0-9]+)?)元", excerpt,
        ))
    deductions.update(Decimal(value) for value in re.findall(
        r"按A股除权前总股本[^。]{0,60}计算的每股派息\(含税\):([0-9]+(?:\.[0-9]+)?)元", text,
    ))
    if cash and shares and paired_references:
        if len(paired_references) != 1:
            return None
        reference = paired_references.pop()
        return reference if deductions == {reference.cash_per_share} else None
    if len(deductions) == 1 and shares == 0:
        return _ExReference(deductions.pop(), Decimal(0))
    share_references = {Decimal(value) for value in re.findall(r"收盘价/\(1\+([0-9]+(?:\.[0-9]+)?)\)", text)}
    if len(share_references) == 1 and cash == 0 and shares > 0:
        return _ExReference(Decimal(0), share_references.pop())
    if not deductions and uniform and not re.search(r"差异化分红送转:是|回购[^。]{0,60}不参与|不参与(?:本次)?(?:分红|利润分配|权益分派)", text):
        return _ExReference(cash, shares)
    return None


def _document_terms(document: AnnouncementText) -> _ImplementationTerms | str:
    text = document.normalized_text
    repeated_header = "证证券券代代码码::" + "".join(char * 2 for char in document.symbol)
    first_page = normalize_announcement_text("".join(document.pages[:1]))
    if not re.search(rf"(?:(?:证券|股票|A股)代码|证券号码):(?:[0-9]{{6}}/)*{document.symbol}(?![0-9])", first_page) and repeated_header not in first_page:
        return "pdf_security_identity_mismatch"
    if re.search(r"本次(?:权益分[派配]|利润分配|分配|转增)(?:方案)?(?:已经|已)?(?:取消|终止)", text):
        return "separate_revision_requires_review"
    if ("预案" in document.title or re.search(r"本次(?:权益分[派配]|利润分配|分配|转增)(?:方案|预案)?"
        r"(?:尚|还)需[^。]{0,60}(?:审议|批准)[^。]{0,30}(?:实施|生效)", text)):
        return "implementation_stage_unconfirmed"
    special = bool(re.search(r"重整|(?<!转)债转股|偿债|以股抵债", text))
    if special:
        all_excluded = re.search(
            r"(?:上述|前述|本次)(?:资本公积金?转增的股份|转增(?:的)?(?:股票|股份))"
            r"(?:[0-9,.]+股)?不向原股东分配[,;]全部", text)
        if (not all_excluded or re.search(r"第[一二三四]轮转增|业绩[^。]{0,30}补偿", text)
            or re.search(r"(?<!不)向(?:原|普通|中小|全体)股东[^。]{0,40}每(?:10)?股[^。]{0,20}(?:转增|送股)", text)
            or re.search(r"(?<!不)(?:向|给予|对)(?:原|普通|中小|全体)股东[^。]{0,40}(?:补偿|补赠|转赠|额外分配)", text)):
            return "special_beneficiary_requires_review"
        if any(_per_share(pattern, text) - {Decimal(0)} for pattern in _CASH):
            return "distribution_amount_conflict"
        if not re.search(r"不涉及现金红利|不(?:派发|进行|派送)现金(?:红利|分红)|不分配现金", text):
            allocation = re.search(r"[一二三四五六七八九十][、.]资本公积金?转增股本方案(.+?)"
                r"(?=[一二三四五六七八九十][、.])", text)
            if not allocation or all_excluded.group() not in allocation[1]:
                return "ordinary_cash_entitlement_unconfirmed"
            plan = allocation[1]
            # Investor consideration paid to the issuer is not an ordinary shareholder dividend.
            cash_scope = re.sub(r"(?:重整|产业|财务)投资人[^。;]{0,80}(?:支付|缴纳)(?:给|至)?(?:上市)?公司"
                r"[^。;]{0,60}(?:现金|对价)[^。;]*", "", plan)
            if (not re.search(r"用于(?:引入(?:重整|产业|财务)投资人|偿付债务|清偿债务|抵偿债务)", plan)
                or re.search(r"另行(?:公告|披露|确定)|现金|红利|股利|派息|派现", cash_scope)
                or re.search(r"(?:(?:向|给予|对)(?:原|普通|中小|全体)?股东|"
                    r"(?:原|普通|中小|全体)股东(?:将|可|有权)(?:获得|获派|取得))[^。]{0,60}(?:现金|派息|派现)", text)):
                return "ordinary_cash_entitlement_unconfirmed"
        cash = shares = Decimal(0)
        implicit_zero_cash = False
        plan = text
    else:
        if not re.search(r"(?:登记在册|登记在)[^。]{0,80}全体[^。]{0,10}股东", text):
            return "shareholder_scope_unclear"
        sections = re.split(r"[一二][、.]\s*(?:本次实施的)?(?:权益分[派配]方案|分配方案|利润分配(?:及资本公积金?转增股本)?方案|资本公积金转增股本方案)", text)
        plan = re.split(r"[二三][、.]", sections[-1], maxsplit=1)[0] if len(sections) == 2 else text
        latest = tuple(re.finditer(r"(?:(?:按最新股本计算的|调整后的)[^:。]{0,40}(?:权益分派|利润分配)方案为|"
            r"(?:权益分派|利润分配)方案调整为|分派变更如下|方案进行了调整,[^:。]{0,50}方案为|"
            r"固定不变[”\"]?的原则,[^:。]{0,40}(?:权益分[派配]|利润分配)方案为):|"
            r"因此,公司应(?=以[^。]{0,60}总股本)|根据以上情况,现重新计算每10股派发的现金红利|"
            r"(?=调整后每股现金分红(?:约)?为[0-9]+(?:\.[0-9]+)?元\(含税)", plan))
        if len(latest) > 1:
            return "distribution_amount_ambiguous"
        if latest:
            plan = plan[latest[0].end():]
        cash_values = set().union(*(_per_share(pattern, plan) for pattern in _CASH))
        share_values = [set().union(*(_per_share(pattern, plan) for pattern in patterns)) for patterns in _SHARES]
        if len(cash_values) > 1 or any(len(values) > 1 for values in share_values):
            return "distribution_amount_conflict"
        for patterns, zero_statement, values in zip(_SHARES,
                (r"不(?:送红股|送股|派送红股)", r"不(?:以)?(?:进行)?(?:资本公积金?)?转增(?:股本|股份)"),
                share_values, strict=True):
            if not values and any(value for pattern in patterns for value in _per_share(pattern, text)) and not re.search(zero_statement, plan):
                return "distribution_amount_missing"
        if not cash_values and not any(share_values):
            return "distribution_amount_missing"
        implicit_zero_cash = not cash_values and not re.search(r"不(?:派发|进行|派送)现金(?:红利|分红)|不分配现金", plan)
        if implicit_zero_cash and (not any(value > 0 for values in share_values for value in values)
            or re.search(r"现金|派息|派(?:发|送)?[^。]{0,20}(?:红利|股利)", plan)):
            return "distribution_amount_missing"
        cash = next(iter(cash_values), Decimal(0))
        headline_cash = {Decimal(value) for value in re.findall(
            r"A股每股现金(?:红利|分红)(?:为|:)?(?:人民币)?([0-9]+(?:\.[0-9]+)?)元", text)}
        summary = re.split(r"[一二][、.]", text, maxsplit=1)[0]
        headline_cash.update(Decimal(value) for value in re.findall(
            r"(?:每股分配比例:?|重要内容提示:)每股现金(?:红利|分红)(?:为|:)?(?:人民币)?([0-9]+(?:\.[0-9]+)?)元", summary))
        if headline_cash and headline_cash != {cash}:
            return "distribution_amount_conflict" if cash_values else "distribution_amount_missing"
        shares = sum((next(iter(values), Decimal(0)) for values in share_values), Decimal(0))
    records = _dates(r"(?:股权|权益(?:分派)?)登记日期?(?:为|即|:|\(){0,3}", text)
    ex_dates = _dates(r"除(?:权除息日(?:\(红利发放日\))?|权日\(除息日\)|权\(息\)日|息日|权日)(?:为|:){0,3}", text)
    pay_dates = _dates(r"(?:现金(?:红利|分红|股利)|A股股东红利|股息|本次红利委托中国结算深圳分公司)(?:将)?于", text)
    # A table's last header must not consume the first registration-date cell after text joining.
    pay_dates.update(_dates(r"(?:现金)?红利发放日\)?(?:为|:){1,3}", text))
    listing_dates = _dates(r"(?:新增(?:可流通股(?:份)?|股份)上市日(?:\(红利发放日\))?|转增股(?:本|份)上市日|"
        r"无限售(?:条件)?流通股的起始交易日)(?:为|:){1,3}", text)
    listing_dates.update(date(int(year), int(month), int(day)) for year, month, day in re.findall(
        r"本次所送\(转\)的无限售(?:条件)?流通股的起始交易日(?:为|:){0,3}" + _DATE + r"(?=。)", text))
    for page in document.pages:
        preceding = ""
        for line in page.splitlines():
            line = normalize_announcement_text(line)
            if line.startswith("A股"):
                line = line.split("B股", maxsplit=1)[0]
            if re.match(r"(?:A股|20\d{2}[/年])", line):
                dates = [date(int(y), int(m), int(d)) for y, m, d in re.findall(_DATE, line)]
                if len(dates) == 3 and "B股" not in line:
                    records.add(dates[0])
                    ex_dates.add(dates[1])
                    if cash:
                        pay_dates.add(dates[2])
                    if shares:
                        listing_dates.add(dates[2])
                elif len(dates) == 4 and cash and shares and "B股" not in line:
                    # Four-date tables distinguish payment from listing; column order is not fixed.
                    header = preceding.rsplit("股份类别", maxsplit=1)[-1]
                    header = header.replace("除权除息日", "除权(息)日").replace(
                        "新增可流通股份上市日", "新增无限售条件流通股份上市日")
                    header = re.sub(r"(?<!现金)红利发放日", "现金红利发放日", header)
                    labels = ("股权登记日", "除权(息)日", "新增无限售条件流通股份上市日", "现金红利发放日")
                    if all(header.count(label) == 1 for label in labels):
                        ordered = sorted(labels, key=header.index)
                        values = dict(zip(ordered, dates, strict=True))
                        records.add(values[labels[0]])
                        ex_dates.add(values[labels[1]])
                        listing_dates.add(values[labels[2]])
                        pay_dates.add(values[labels[3]])
            preceding = (preceding + line)[-400:]
    if special:
        ex_dates.update(_dates(r"股权登记日次一交易日\(", text))
        if len(records) == 1 and re.search(r"股权登记日次一交易日[^。;]{0,40}(?:开盘参考价|除权)", text):
            # A one-session suspension on registration links resumption to the next trading session.
            resumptions = {date(int(y2), int(m2), int(d2)) for y1, m1, d1, y2, m2, d2 in re.findall(
                _DATE + r"(?:公司)?股票(?:开市起)?停牌1个交易日[,;]并于" + _DATE
                + r"(?:\([^)]*\))?(?:开市起)?复牌", text) if date(int(y1), int(m1), int(d1)) in records}
            if resumptions and listing_dates:
                if resumptions != listing_dates or len(resumptions) != 1:
                    return "registration_or_ex_date_ambiguous"
                ex_dates.update(resumptions)
    if len(records) > 1 or len(ex_dates) > 1:
        return "registration_or_ex_date_ambiguous"
    if not records or not ex_dates:
        return "registration_or_ex_date_missing"
    if implicit_zero_cash and pay_dates:
        return "distribution_amount_missing"
    if (cash and len(pay_dates) > 1) or (shares and len(listing_dates) > 1):
        return "settlement_date_ambiguous"
    if (cash and not pay_dates) or (shares and not listing_dates):
        return "settlement_date_missing"
    reference = None if special else _reference_terms(text, plan, cash, shares)
    terms = DistributionTerms(published_date=document.published_date, published_ts_us=document.published_ts_us, cash_per_share=cash,
        shares_per_share=shares, reference_cash_per_share=reference.cash_per_share if reference else None,
        reference_shares_per_share=reference.shares_per_share if reference else None)
    return _ImplementationTerms(records.pop(), ex_dates.pop(), pay_dates.pop() if cash else None,
                                listing_dates.pop() if shares else None, terms)


def _reference_result(document: AnnouncementText) -> _ExReference | Decimal | str:
    text = document.normalized_text
    if not re.search(rf"(?:证券|股票|A股)代码:{document.symbol}(?![0-9])",
                     normalize_announcement_text("".join(document.pages[:1]))):
        return "pdf_security_identity_mismatch"
    prices = {Decimal(value) for value in re.findall(
        r"(?:除权(?:除息|\(息\))?(?:参考)?价格?|开盘参考价格?)(?:确定)?为(?:每股)?"
        r"([0-9]+(?:\.[0-9]+)?)元", text)}
    if len(prices) == 1:
        return prices.pop()
    if len(prices) > 1:
        return "reference_result_conflict"
    no_adjustment = (r"(?:参考价格?|除权)(?:不(?:作|做|进行)?调整|无需调整)|"
        r"不(?:作|做|进行)?调整[^。;]{0,60}(?:开盘参考价格?|除权参考价格?)|不除权")
    conditional = r"如果|(?:倘)?若|假如|一旦|仅当|只有|当[^。;]{0,80}时"
    if ((re.search(no_adjustment, document.title) and not re.search(conditional, document.title))
        or any(re.search(no_adjustment, sentence) and not re.search(conditional, sentence)
            and re.search(r"鉴于|经计算|计算结果|因此|据此", sentence) for sentence in re.split(r"[。;]", text))):
        return _ExReference(Decimal(0), Decimal(0))
    return "ex_reference_not_confirmed"


def extract_distribution_review(
    records: Sequence[DividendRecord], documents: Sequence[AnnouncementText],
    identities: Sequence[SecurityIdentity] = (),
) -> DistributionExtraction:
    """Associate implementation facts by their publication, registration and security.

    Example:
        extracted = extract_distribution_review(records=source_group, documents=pdfs)
        if extracted.review is None:
            print(extracted.reason)
    """
    source = records[0]
    aliases = {row.published_symbol: row for row in identities if row.symbol == source.symbol}
    source_symbols = {source.symbol, *aliases}
    year, month = source.end_date.year, source.end_date.month
    period = {3: "第一季度|一季度|季度", 6: "上?半年度|(?:度)?(?:第[一二三四]次)?中期|季度",
              9: "前三季度|第三季度|三季度|季度", 12: "年度"}.get(month)
    implementation_dates = {row.imp_ann_date for row in records if row.imp_ann_date is not None}
    if any(document.symbol in source_symbols and re.search(r"业绩.*补偿", document.title)
        and _mentions_date(document.normalized_text, source.record_date) for document in documents):
        return DistributionExtraction(None, "compensation_requires_review")
    distribution_topic = r"权益分[派配]|利润分[配派]|分红派息|特别派息|现金分红|转增股本"
    matching = []
    for document in documents:
        title = normalize_announcement_text(document.title)
        if document.symbol not in source_symbols:
            continue
        if any(word in document.title for word in ("转股价格", "律师", "法律意见", "核查", "独立财务", "专项意见", "结果")):
            continue
        if re.search(r"(?:回购(?:股份)?|发行|授予|行权)价(?:格)?", document.title):
            continue
        if "更正公告" in document.title and "更正后" not in document.title:
            continue
        text = document.normalized_text
        if not re.search(distribution_topic, title) and document.published_date not in implementation_dates:
            continue
        if re.search(r"除权|参考价", document.title) and not re.search(distribution_topic + r".*实施", document.title):
            continue
        if document.published_date not in implementation_dates and not (
            period is not None and re.search(rf"{year}年?(?:{period})", title)
        ):
            if re.search(r"20\d{2}", title):
                continue
            plan_periods = re.findall(r"20\d{2}年?(?:年度|上?半年度|下半年度|第?[一二三四]季度|"
                r"(?:度)?(?:第[一二三四]次)?中期|季度)(?:权益分[派配]|利润分配|分红派息)方案", text)
            if period is None or not plan_periods or any(not re.match(rf"{year}年?(?:{period})", value) for value in plan_periods):
                continue
        if not _mentions_date(text, source.record_date):
            continue
        matching.append(document)
    if not matching:
        reason = "implementation_event_not_matched" if any(document.symbol in source_symbols for document in documents) else "missing_implementation"
        return DistributionExtraction(None, reason)
    reference_documents = [document for document in documents if document.symbol in source_symbols
        and document not in matching and re.search(r"除权|参考价", document.title)
        and re.search(r"结果|提示|不(?:作|做|进行)?调整|无需调整|不除权", document.title)
        and not re.search(r"更正|补充|修订|取消|终止", document.title)
        and (_mentions_date(document.normalized_text, source.record_date)
            or any(normalize_announcement_text(original.title) in document.normalized_text
                and _mentions_date(document.normalized_text, original.published_date) for original in matching))]
    matching.sort(key=lambda document: (document.published_date, document.published_ts_us is None,
        document.published_ts_us or 0, document.announcement_id))
    for document in documents:
        if (document.symbol not in source_symbols or document in matching or document in reference_documents
            or document.published_date < matching[0].published_date
            or not re.search(r"更正|补充|修订|调整|取消|终止", document.title)
            or re.search(r"(?:回购(?:股份)?|转股|发行|授予|行权)价(?:格)?", document.title)):
            continue
        text = document.normalized_text
        title = normalize_announcement_text(document.title)
        revises_distribution = re.search(
            r"(?:权益分[派配]|利润分配|分红派息|转增股本)(?:预案|方案)?(?:实施)?(?:公告)?的?"
            r"(?:更正|补充|修订|调整|取消|终止)|(?:更正|补充|修订|调整|取消|终止)(?:公司)?"
            r"(?:20\d{2}[^。:]{0,12})?(?:权益分[派配]|利润分配|分红派息|转增股本)", title)
        if not revises_distribution:
            continue
        identifies_plan = (period is not None and re.search(rf"{year}年?(?:{period})", title)
            and re.search(distribution_topic, title) and "实施" in title)
        references_plan = any(normalize_announcement_text(original.title) in text
            and _mentions_date(text, original.published_date) for original in matching)
        if (_mentions_date(text, source.record_date) or identifies_plan or references_plan) and re.search(r"权益分派|利润分配|分红派息|转增股本", text):
            total_correction = re.search(r"更正前:(.+?)更正后:(.+?)(?:除以上|除上述|除此之外)", text)
            if (total_correction and re.search(r"(?:原公告)?其他内容不变", text)
                and all(re.fullmatch(r"本次权益分派新增无限售条件流通股(?:股数)?(?:为|:)[0-9,.]+股。", section)
                    for section in total_correction.groups())):
                continue
            return DistributionExtraction(None, f"separate_revision_requires_review:{document.announcement_id}")
    versions: list[DistributionTerms] = []
    phase_dates = None
    used = []
    missing_reason = "missing_implementation"
    for document in sorted((*matching, *reference_documents), key=lambda document: (
        document.published_date, document.published_ts_us is None, document.published_ts_us or 0, document.announcement_id,
    )):
        if document in reference_documents:
            if not versions:
                continue
            reference = _reference_result(document)
            if isinstance(reference, str):
                if reference in {"pdf_security_identity_mismatch", "ex_reference_not_confirmed"}:
                    continue
                return DistributionExtraction(None, f"{reference}:{document.announcement_id}")
            terms = versions[-1].model_copy(update={"published_date": document.published_date,
                "published_ts_us": document.published_ts_us,
                "reference_cash_per_share": reference.cash_per_share if isinstance(reference, _ExReference) else None,
                "reference_shares_per_share": reference.shares_per_share if isinstance(reference, _ExReference) else None,
                "reference_price": reference if isinstance(reference, Decimal) else None})
        else:
            parsed = _document_terms(document)
            if isinstance(parsed, str):
                if parsed in {"distribution_amount_missing", "settlement_date_missing",
                    "registration_or_ex_date_missing", "shareholder_scope_unclear", "implementation_stage_unconfirmed",
                    "ordinary_cash_entitlement_unconfirmed"}:
                    if versions and re.search(r"更正|补充|修订", document.title):
                        return DistributionExtraction(None, f"separate_revision_requires_review:{document.announcement_id}")
                    missing_reason = f"{parsed}:{document.announcement_id}"
                    continue
                return DistributionExtraction(None, f"{parsed}:{document.announcement_id}")
            record_date, ex_date, pay_date, listing_date, terms = parsed
            if record_date != source.record_date:
                return DistributionExtraction(None, "announcement_registration_mismatch")
            # An impossible past-year payment is evidence of a PDF defect, not a new payment schedule.
            if (len(matching) == 1 and not reference_documents and pay_date is not None
                and pay_date < document.published_date and pay_date.year < record_date.year
                and terms.cash_per_share and terms.shares_per_share == 0
                and terms.reference_cash_per_share == terms.cash_per_share
                and terms.reference_shares_per_share == 0
                and all(row.imp_ann_date == document.published_date and row.ex_date == ex_date
                    and row.cash_div_tax == terms.cash_per_share and row.stk_div == 0
                    and row.pay_date == source.pay_date for row in records)
                and source.pay_date is not None and source.pay_date >= ex_date
                and source.pay_date.year == record_date.year
                and (source.pay_date.month, source.pay_date.day) == (pay_date.month, pay_date.day)):
                return DistributionExtraction(None, f"announcement_payment_year_conflict:{document.announcement_id}:"
                    f"pdf={pay_date}:dividend={source.pay_date}")
            dates = (record_date, ex_date, pay_date, listing_date)
            if phase_dates is not None and dates != phase_dates:
                return DistributionExtraction(None, "phase_date_revision_requires_review")
            phase_dates = dates
        used.append(document)
        if versions:
            prior = versions[-1]
            if terms.model_dump(exclude={"published_date", "published_ts_us"}) == prior.model_dump(
                exclude={"published_date", "published_ts_us"}
            ):
                continue
            if terms.published_date == prior.published_date and (terms.published_ts_us is None
                or prior.published_ts_us is None or terms.published_ts_us <= prior.published_ts_us):
                return DistributionExtraction(None, "same_day_revision_order_unknown")
        versions.append(terms)
    if not versions:
        return DistributionExtraction(None, missing_reason)
    has_reference = any(terms.reference_cash_per_share is not None or terms.reference_price is not None for terms in versions)
    if not has_reference and any(terms.cash_per_share or terms.shares_per_share for terms in versions):
        return DistributionExtraction(None, "ex_reference_not_confirmed")
    reason = "implementation_pdf_terms_and_reference_confirmed" if has_reference else "ordinary_shareholder_zero_entitlement_confirmed"
    try:
        evidence = [document.url for document in used]
        evidence.extend(sorted({aliases[document.symbol].evidence_url
            for document in used if document.symbol != source.symbol}))
        review = CorporateActionReview(candidate_id=source.candidate_id,
            action_id=f"{used[0].source}:{used[0].announcement_id}:ordinary_shareholder", shareholder_scope="ordinary_shareholder",
            record_date=record_date, ex_date=ex_date, pay_date=pay_date, div_listdate=listing_date, terms=tuple(versions),
            evidence_refs=tuple(evidence),
            reason=reason)
    except ValidationError as error:
        return DistributionExtraction(None, f"unsupported_announcement_timeline:{error.errors(include_url=False)}")
    return DistributionExtraction(review, reason)
