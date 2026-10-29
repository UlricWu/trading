# filepath: src/data_system/corporate_actions.py
"""Resolve trusted ordinary dividend facts and apply confirmed corrections."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.data_system.announcement_publications import AnnouncementPublication
from src.utils.datetime_utils import DateTimeUtils


class DividendRecord(BaseModel):
    """One normalized source row, whose identity is not an account event.

    Example:
        row = DividendRecord.model_validate(normalized_row)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    ts_code: str = Field(pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    end_date: date
    ann_date: date | None
    div_proc: str
    imp_ann_date: date | None
    record_date: date
    ex_date: date | None
    pay_date: date | None
    div_listdate: date | None
    cash_div_tax: Decimal | None = Field(ge=0, allow_inf_nan=False)
    stk_div: Decimal | None = Field(ge=0, allow_inf_nan=False)
    base_date: date | None = None
    base_share: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @property
    def candidate_id(self) -> str:
        """Identify a group to investigate, without asserting a unique event.

        Example:
            key = row.candidate_id
        """
        return f"{self.ts_code}/{self.end_date}/{self.record_date}"


class DistributionTerms(BaseModel):
    """Publicly dated terms for an ordinary secondary-market shareholder.

    Example:
        terms = DistributionTerms(published_date="2024-07-18", cash_per_share="0.3158",
            shares_per_share="0", reference_cash_per_share="0.28", reference_shares_per_share="0")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    published_date: date
    published_ts_us: StrictInt | None = None
    cash_per_share: Decimal | None = Field(ge=0, allow_inf_nan=False)
    shares_per_share: Decimal | None = Field(ge=0, allow_inf_nan=False)
    reference_cash_per_share: Decimal | None = Field(ge=0, allow_inf_nan=False)
    reference_shares_per_share: Decimal | None = Field(ge=0, allow_inf_nan=False)
    reference_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    benefit_state: Literal["conditional", "confirmed", "cancelled"] = "confirmed"
    pay_date: date | None = None
    shares_credit_date: date | None = None
    shares_available_date: date | None = None
    shares_numerator: StrictInt | None = Field(default=None, ge=0)
    shares_denominator: StrictInt | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_publication(self) -> Self:
        if (self.shares_numerator is None) != (self.shares_denominator is None):
            raise ValueError("share allocation requires both numerator and denominator")
        if ((self.reference_cash_per_share is None) != (self.reference_shares_per_share is None)
            or (self.reference_price is not None and self.reference_cash_per_share is not None)):
            raise ValueError("reference requires a complete formula pair or an absolute price")
        if self.published_ts_us is not None and DateTimeUtils.to_local(
            DateTimeUtils.from_utc_epoch_us(self.published_ts_us)
        ).date() != self.published_date:
            raise ValueError("exact publication time must match the local publication date")
        return self

    @property
    def available_ts_us(self) -> int:
        """Use a verified instant or the declared conservative date-only availability.

        Example:
            wakeup = terms.available_ts_us
        """
        return (self.published_ts_us if self.published_ts_us is not None else
            DateTimeUtils.local_time_to_utc_epoch_us(time(), self.published_date + timedelta(days=1)))


class CorporateActionReview(BaseModel):
    """Confirmed distribution associated with source candidates or an original announcement.

    Example:
        review = CorporateActionReview.model_validate_json(review_json)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str = Field(min_length=1)
    announcement_source: AnnouncementPublication | None = None
    related_sources: tuple[str, ...] = ()
    action_id: str = Field(min_length=1)
    shareholder_scope: Literal["ordinary_shareholder"]
    kind: Literal["distribution", "compensation"] = "distribution"
    record_date: date
    ex_date: date | None
    pay_date: date | None
    div_listdate: date | None
    terms: tuple[DistributionTerms, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_timeline(self) -> Self:
        if self.announcement_source is not None:
            source = self.announcement_source
            identity = f"{source.source}:{source.announcement_id}"
            if (self.candidate_id != identity or self.action_id != identity + ":ordinary_shareholder"
                or source.url not in self.evidence_refs):
                raise ValueError("announcement source must retain its native identity and original evidence")
            if (self.terms[0].published_date < source.published_date or (source.published_ts_us is not None
                and self.terms[0].available_ts_us < source.published_ts_us)):
                raise ValueError("announcement source cannot publish terms before its original disclosure")
        if self.candidate_id in self.related_sources:
            raise ValueError("primary source must not be repeated in related_sources")
        if len(set(self.related_sources)) != len(self.related_sources):
            raise ValueError("related sources must be unique")
        times = [terms.available_ts_us for terms in self.terms]
        if times != sorted(set(times)):
            raise ValueError("review terms must have unique, increasing publication times")
        if self.kind == "compensation":
            if self.pay_date is not None or self.div_listdate is not None:
                raise ValueError("compensation settlement dates belong to their public terms version")
            for terms in self.terms:
                if terms.shares_numerator is not None and terms.shares_per_share is None:
                    raise ValueError("confirmed allocation formula requires a known share ratio")
                if any(day is not None and day < self.record_date for day in
                    (terms.pay_date, terms.shares_credit_date, terms.shares_available_date)):
                    raise ValueError("compensation settlement cannot precede eligibility")
            for prior, terms in zip(self.terms, self.terms[1:]):
                if prior.benefit_state == "cancelled" and terms.benefit_state != "cancelled":
                    raise ValueError("cancelled compensation requires a separate event")
                if prior.benefit_state == "confirmed":
                    if terms.benefit_state != "confirmed":
                        raise ValueError("confirmed compensation cannot become conditional or cancelled")
                    for field in ("cash_per_share", "shares_per_share", "shares_numerator", "shares_denominator"):
                        known = getattr(prior, field)
                        if known is not None and getattr(terms, field) is None:
                            raise ValueError("known compensation amounts cannot become unknown")
            return self
        if self.ex_date is None:
            raise ValueError("ordinary distribution requires an ex date")
        if any(any(value is not None for value in
            (terms.pay_date, terms.shares_credit_date, terms.shares_available_date,
             terms.shares_numerator, terms.shares_denominator)) for terms in self.terms):
            raise ValueError("compensation terms cannot be used for ordinary distributions")
        if self.record_date >= self.ex_date:
            raise ValueError("review requires registration before ex date")
        for prior, terms in zip(self.terms, self.terms[1:]):
            amounts = ((prior.cash_per_share, terms.cash_per_share), (prior.shares_per_share, terms.shares_per_share))
            if any(old is not None and new is None for old, new in amounts):
                raise ValueError("known distribution amounts cannot become unknown")
        for terms in self.terms:
            for amount, settlement, field in (
                (terms.cash_per_share, self.pay_date, "pay_date"),
                (terms.shares_per_share, self.div_listdate, "div_listdate"),
            ):
                if settlement is not None and (amount == 0 or settlement < self.ex_date):
                    raise ValueError(f"review {field} must follow ex date and match its benefit")
        return self


class CorporateActionResolution(BaseModel):
    """One candidate's source-based, reviewed, or unresolved disposition.

    Example:
        resolution = CorporateActionResolution.model_validate_json(stored_resolution)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    record_date: date
    status: Literal["resolved", "not_entitled", "unresolved"]
    reason: str
    review: CorporateActionReview | None
    source_record: DividendRecord | None = None

    @model_validator(mode="after")
    def _validate_review(self) -> Self:
        origins = int(self.review is not None) + int(self.source_record is not None)
        if origins != (0 if self.status == "unresolved" else 1):
            raise ValueError("executable candidates require exactly one dividend source or announcement review")
        independent = (self.review is not None and self.review.announcement_source is not None
            and self.candidate_id == self.review.candidate_id)
        if self.review is not None:
            if (self.candidate_id not in (self.review.candidate_id, *self.review.related_sources)
                or self.review.record_date != self.record_date):
                raise ValueError("resolution review does not identify its source group")
            origin = self.review.announcement_source
            if origin is not None and independent and self.symbol != origin.symbol:
                raise ValueError("independent announcement security differs from its source")
            no_right = all(t.cash_per_share == 0 and t.shares_per_share == 0 for t in self.review.terms)
            if (self.status == "not_entitled") != no_right:
                raise ValueError("not_entitled must match zero ordinary-shareholder rights")
        if self.source_record is not None:
            row = self.source_record
            if (row.candidate_id != self.candidate_id or row.symbol != self.symbol
                or row.record_date != self.record_date
                or row.div_proc != "实施" or _unresolved_reason((row,)) is not None):
                raise ValueError("executable dividend source must identify one complete ordinary distribution")
            if (self.status == "not_entitled") != (not row.cash_div_tax and not row.stk_div):
                raise ValueError("not_entitled must match explicit zero dividend rights")
        return self

    @property
    def action_id(self) -> str | None:
        """Identify the executable event without presenting a source row as a review.

        Example:
            event_id = resolution.action_id
        """
        if self.review is not None:
            return self.review.action_id
        if self.source_record is not None:
            return f"dividend:{self.candidate_id}:ordinary_shareholder"
        return None


def resolve_corporate_actions(
    records: Sequence[DividendRecord], reviews: Sequence[CorporateActionReview],
) -> tuple[CorporateActionResolution, ...]:
    """Resolve complete ordinary source facts, giving matching confirmed reviews priority.

    Example:
        resolutions = resolve_corporate_actions(source_rows, announcement_reviews)
    """
    grouped: dict[str, list[DividendRecord]] = {}
    for row in records:
        if row.div_proc == "实施":
            grouped.setdefault(row.candidate_id, []).append(row)
    by_candidate: dict[str, CorporateActionReview] = {}
    for review in reviews:
        for candidate in (review.candidate_id, *review.related_sources):
            if candidate in by_candidate:
                raise ValueError("corporate-action reviews must have unique candidate_id bindings")
            by_candidate[candidate] = review
    independent = {review.candidate_id: review for review in reviews if review.announcement_source is not None}
    if by_candidate.keys() - grouped.keys() - independent.keys():
        raise ValueError("corporate-action review has no matching implemented source group")
    if len({review.action_id for review in reviews}) != len(reviews):
        raise ValueError("corporate-action reviews must have unique action_id")
    for review in reviews:
        identities = {("bse" if grouped[key][0].ts_code.endswith(".BJ") else "cninfo",
            grouped[key][0].symbol, grouped[key][0].record_date)
            for key in (review.candidate_id, *review.related_sources) if key in grouped}
        if review.announcement_source is not None:
            identities.add((review.announcement_source.source, review.announcement_source.symbol, review.record_date))
        if len(identities) != 1:
            raise ValueError("related sources must identify the same security and registration date")
    candidates_by_registration: dict[tuple[str, date], set[str]] = {}
    for candidate, rows in grouped.items():
        candidates_by_registration.setdefault((rows[0].symbol, rows[0].record_date), set()).add(candidate)
    for review in independent.values():
        candidates_by_registration.setdefault(
            (review.announcement_source.symbol, review.record_date), set()).add(review.candidate_id)
    resolutions: list[CorporateActionResolution] = []
    for candidate_id, rows in sorted(grouped.items()):
        review = by_candidate.get(candidate_id)
        selected = _select_source_record(tuple(row for row in rows if row.imp_ann_date is not None))
        if selected is not None:
            publication = cast(date, selected.imp_ann_date)
            # An implementation supersedes older plans; an undated or later incomplete
            # observation must not silently supply or erase a known implementation fact.
            rows = [row for row in rows if (
                row.imp_ann_date is not None and row.imp_ann_date >= publication
            ) or (row.imp_ann_date is None and (row.ann_date is None or row.ann_date >= publication))]
        reason = _unresolved_reason(rows)
        source_record = None
        status: Literal["resolved", "not_entitled", "unresolved"] = "unresolved"
        if review is not None:
            reason = review.reason
            status = "not_entitled" if all(t.cash_per_share == 0 and t.shares_per_share == 0 for t in review.terms) else "resolved"
        elif reason is None:
            if len(candidates_by_registration[(rows[0].symbol, rows[0].record_date)]) > 1:
                reason = "same_registration_candidates_require_review"
            else:
                source_record = cast(DividendRecord, _select_source_record(rows))
                reason = "ordinary_dividend_source"
                status = "resolved" if source_record.cash_div_tax or source_record.stk_div else "not_entitled"
        resolutions.append(CorporateActionResolution(candidate_id=candidate_id, symbol=rows[0].symbol,
            record_date=rows[0].record_date,
            status=status, reason=reason, review=review, source_record=source_record))
    for review in reviews:
        origin = review.announcement_source
        if origin is None:
            continue
        resolutions.append(CorporateActionResolution(candidate_id=review.candidate_id, symbol=origin.symbol,
            record_date=review.record_date,
            status="not_entitled" if all(t.cash_per_share == 0 and t.shares_per_share == 0 for t in review.terms) else "resolved",
            reason=review.reason, review=review))
    source_actions = {item.action_id for item in resolutions if item.source_record is not None}
    if source_actions & {review.action_id for review in reviews}:
        raise ValueError("source and reviewed events must have unique action_id")
    return tuple(sorted(resolutions, key=lambda item: item.candidate_id))


def _select_source_record(rows: Sequence[DividendRecord]) -> DividendRecord | None:
    """Prefer explicit amounts, then a stock-only observation without rewriting its cash null."""
    complete = [row for row in rows if row.cash_div_tax is not None and row.stk_div is not None]
    if not complete and not any(row.cash_div_tax for row in rows):
        complete = [row for row in rows if row.cash_div_tax is None and row.pay_date is None
            and row.stk_div is not None and row.stk_div > 0]
    return max(complete, key=lambda row: (cast(date, row.imp_ann_date), row.ann_date or date.min), default=None)


def _unresolved_reason(rows: Sequence[DividendRecord]) -> str | None:
    if any(row.imp_ann_date is None or row.ex_date is None for row in rows):
        return "missing_implementation_dates"
    if (any((row.end_date.month, row.end_date.day) not in ((3, 31), (6, 30), (9, 30), (12, 31)) for row in rows)
        and not any(row.cash_div_tax is not None and row.cash_div_tax > 0 and row.stk_div == 0 for row in rows)):
        return "nonstandard_distribution_requires_review"
    if (len({row.cash_div_tax for row in rows if row.cash_div_tax is not None}) > 1
        or len({row.stk_div for row in rows if row.stk_div is not None}) > 1):
        return "different_distribution_terms"
    if len({(row.ex_date, row.pay_date, row.div_listdate) for row in rows}) > 1:
        return "different_phase_dates"
    row = _select_source_record(rows)
    if row is None:
        return "missing_distribution_terms"
    if row.imp_ann_date >= row.record_date or row.record_date >= row.ex_date:
        return "unsupported_source_timeline"
    for amount, settlement in ((row.cash_div_tax, row.pay_date), (row.stk_div, row.div_listdate)):
        if bool(amount) != (settlement is not None) or (settlement is not None and settlement < row.ex_date):
            return "invalid_source_settlement_dates"
    return None
