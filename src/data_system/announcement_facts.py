# filepath: src/data_system/announcement_facts.py
"""Partial public facts and their event views, independent of account readiness."""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from src.data_system.announcement_publications import AnnouncementPublication
from src.data_system.distribution_announcements import (
    AnnouncementText, _CASH, _document_terms, _dates, _mentions_date, _per_share, _reference_result,
    normalize_announcement_text,
)


class AnnouncementFacts(BaseModel):
    """One disclosure's observations; null means undisclosed, never zero.

    An event view uses the same fields with cumulative evidence. A pool can be known
    before its denominator; only account calculation requires a complete formula.

    Example:
        facts = AnnouncementFacts.model_validate_json(payload)
        print(facts.event_id, facts.shares_numerator, facts.shares_denominator)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str = Field(pattern=r"^(?:cninfo|bse):(?:[a-f0-9]+|manual[0-9]+)$")
    publication: AnnouncementPublication
    observed_at: AwareDatetime
    kind: Literal["distribution", "reorganization", "compensation", "other"]
    benefit_state: Literal["announced", "conditional", "confirmed", "cancelled"] | None = None
    record_date: date | None = None
    ex_date: date | None = None
    pay_date: date | None = None
    shares_credit_date: date | None = None
    shares_available_date: date | None = None
    cash_per_share: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    shares_per_share: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    shares_numerator: StrictInt | None = Field(default=None, ge=0)
    shares_denominator: StrictInt | None = Field(default=None, gt=0)
    reference_cash_per_share: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    reference_shares_per_share: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    reference_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    is_revision: bool = False
    conflicts: tuple[str, ...] = ()
    parse_issue: str | None = None
    evidence_refs: tuple[str, ...]

    @property
    def available_ts_us(self) -> int:
        """Return public availability, independently of collection time.

        Example:
            visible = facts.available_ts_us <= decision_ts_us
        """
        return self.publication.available_ts_us


def extract_announcement_facts(
    document: AnnouncementText, *, observed_at: datetime, previous: Sequence[AnnouncementFacts],
) -> AnnouncementFacts:
    """Keep independently stated facts even when a full distribution cannot be calculated.

    Only an explicit title-and-date citation links documents automatically. Missing
    citations retain independent evidence; an issuer or a newer date is not a link.

    Example:
        facts = extract_announcement_facts(document, observed_at=utc_now, previous=earlier)
    """
    text = document.normalized_text
    publication = AnnouncementPublication.model_validate(document.model_dump(exclude={"pages"}))
    related = {row.event_id for row in previous if row.publication.symbol == document.symbol
        and row.available_ts_us <= publication.available_ts_us
        and normalize_announcement_text(row.publication.title) in text
        and _mentions_date(text, row.publication.published_date)}
    kind = ("compensation" if re.search(r"业绩[^。]{0,30}补偿", document.title + text) else
        "reorganization" if "重整" in document.title + text else
        "distribution" if re.search(r"分派|分配|分红|派息|转增|转赠|除权|除息", document.title + text) else "other")
    values: dict[str, object] = {}
    conflicts = {"event_link"} if len(related) > 1 else set()
    state = "announced"
    if re.search(r"(?:本次|上述)(?:权益分派|利润分配|转增|重整|分配)(?:方案|计划)?(?:已|已经)?(?:取消|终止)", text):
        state = "cancelled"
    elif "预案" in document.title or re.search(r"尚需[^。]{0,50}(?:审议|批准)|待(?:确定|批准)", text):
        state = "conditional"
    elif re.search(r"实施|登记日|实际(?:分配|获配)", document.title + text):
        state = "confirmed"
    patterns = {
        "record_date": r"(?:股权|权益(?:分派)?)登记日期?(?:为|即|:|\(){0,3}",
        "ex_date": r"除(?:权除息日|权\(息\)日|息日|权日)(?:为|:){0,3}",
        "pay_date": r"(?:现金)?红利发放日(?:为|:){1,3}",
        "shares_credit_date": r"(?:转增|补偿|获配)股份(?:到账|登记完成)日(?:为|:){1,3}",
        "shares_available_date": r"(?:转增股(?:本|份)|新增股份)上市日(?:为|:){1,3}",
    }
    for field, pattern in patterns.items():
        dates = _dates(pattern, text)
        if len(dates) == 1:
            values[field] = dates.pop()
        elif dates:
            conflicts.add(field)
    parsed = _document_terms(document)
    if not isinstance(parsed, str):
        values.update(record_date=parsed.record_date, ex_date=parsed.ex_date,
            pay_date=parsed.pay_date, shares_available_date=parsed.listing_date,
            cash_per_share=parsed.terms.cash_per_share, shares_per_share=parsed.terms.shares_per_share,
            reference_cash_per_share=parsed.terms.reference_cash_per_share,
            reference_shares_per_share=parsed.terms.reference_shares_per_share)
    else:
        # This clause identifies the ordinary beneficiary; company-wide capital expansion does not.
        ratios = {Decimal(amount) / (10 if unit == "10" else 1) for unit, amount in re.findall(
            r"(?:普通|中小|符合条件的)股东[^。;]{0,40}?(?:实际)?每(10|一)?股(?:实际)?"
            r"(?:获配|获得|转增|分配)([0-9]+(?:\.[0-9]+)?)股", text)}
        if len(ratios) == 1:
            values["shares_per_share"] = ratios.pop()
        elif ratios:
            conflicts.add("shares_per_share")
        if re.search(r"不(?:派发|进行|派送)现金(?:红利|分红)|不分配现金", text):
            values["cash_per_share"] = Decimal(0)
        elif kind == "distribution":
            cash = set().union(*(_per_share(pattern, text) for pattern in _CASH))
            if len(cash) == 1:
                values["cash_per_share"] = cash.pop()
            elif cash:
                conflicts.add("cash_per_share")
        pools = {int(amount.replace(",", "")) for amount in re.findall(
            r"([0-9,]+)股股票将向股权登记日[^。;]{0,100}(?:之外的全体股东|中小股东|普通股东)进行分配", text)}
        if len(pools) == 1:
            values["shares_numerator"] = pools.pop()
        elif pools:
            conflicts.add("shares_numerator")
        denominators = {int(amount.replace(",", "")) for amount in re.findall(
            r"(?:实际受益股东|符合分配条件的股东|参与分配的股东)持股总(?:数|额)(?:为|:)([0-9,]+)股", text)}
        if len(denominators) == 1:
            values["shares_denominator"] = denominators.pop()
        elif denominators:
            conflicts.add("shares_denominator")
    if re.search(r"除权|参考价", document.title):
        reference = _reference_result(document)
        if isinstance(reference, Decimal):
            values["reference_price"] = reference
        elif not isinstance(reference, str):
            values.update(reference_cash_per_share=reference.cash_per_share,
                reference_shares_per_share=reference.shares_per_share)
    issue = parsed if isinstance(parsed, str) and parsed in {
        "pdf_security_identity_mismatch", "distribution_amount_conflict", "distribution_amount_ambiguous",
        "registration_or_ex_date_ambiguous", "settlement_date_ambiguous"} else None
    if issue == "pdf_security_identity_mismatch":
        values.clear()
        state = "announced"
    return AnnouncementFacts(event_id=next(iter(related)) if len(related) == 1 else
        f"{document.source}:{document.announcement_id}", publication=publication, observed_at=observed_at,
        kind=kind, benefit_state=state, is_revision=bool(re.search(r"更正|修订|调整", document.title)),
        conflicts=tuple(sorted(conflicts)), parse_issue=issue, evidence_refs=(document.url,), **values)


def assemble_announcement_events(rows: Sequence[AnnouncementFacts]) -> tuple[AnnouncementFacts, ...]:
    """Fold already visible observations without turning an omission into a withdrawal.

    A different stated value is a conflict unless a later explicit correction replaces
    it. Repeated native identities are idempotent. Caller selects visibility first.

    Example:
        events = assemble_announcement_events(visible_facts)
    """
    events: dict[str, AnnouncementFacts] = {}
    seen: dict[tuple[str, str], AnnouncementFacts] = {}
    fields = ("record_date", "ex_date", "pay_date", "shares_credit_date", "shares_available_date",
        "cash_per_share", "shares_per_share", "shares_numerator", "shares_denominator",
        "reference_cash_per_share", "reference_shares_per_share", "reference_price")
    for row in sorted(rows, key=lambda item: (item.available_ts_us, item.publication.source, item.publication.announcement_id)):
        identity = (row.publication.source, row.publication.announcement_id)
        if identity in seen:
            if seen[identity] != row:
                raise ValueError(f"conflicting observations for announcement {identity}")
            continue
        seen[identity] = row
        prior = events.get(row.event_id)
        if prior is None:
            events[row.event_id] = row
            continue
        if prior.publication.symbol != row.publication.symbol:
            raise ValueError(f"event spans different securities: {row.event_id}")
        values = {}
        conflicts = set(prior.conflicts) | set(row.conflicts)
        for field in fields:
            old, new = getattr(prior, field), getattr(row, field)
            correction = row.is_revision and row.available_ts_us > prior.available_ts_us
            if new is None:
                values[field] = old
            elif old is None or old == new or correction:
                values[field] = new
                if correction:
                    conflicts.discard(field)
            else:
                values[field] = old
                conflicts.add(field)
        if row.reference_price is not None and "reference_price" not in conflicts:
            values["reference_cash_per_share"] = values["reference_shares_per_share"] = None
        elif row.is_revision and row.reference_cash_per_share is not None and row.reference_shares_per_share is not None:
            values["reference_price"] = None
        state = row.benefit_state
        if state in (None, "announced"):
            state = prior.benefit_state
        if prior.benefit_state == "cancelled" and state != "cancelled":
            conflicts.add("benefit_state")
        events[row.event_id] = row.model_copy(update={**values, "benefit_state": state,
            "kind": prior.kind if row.kind == "other" or prior.kind in ("reorganization", "compensation") else row.kind,
            "conflicts": tuple(sorted(conflicts)),
            "evidence_refs": tuple(dict.fromkeys((*prior.evidence_refs, *row.evidence_refs)))})
    return tuple(events[key] for key in sorted(events))
