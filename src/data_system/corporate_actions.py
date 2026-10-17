# filepath: src/data_system/corporate_actions.py
"""Resolve source observations into evidenced ordinary-shareholder distributions."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    source_row_id: str = Field(pattern=r"^[a-f0-9]{64}$")

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
    cash_per_share: Decimal = Field(ge=0, allow_inf_nan=False)
    shares_per_share: Decimal = Field(ge=0, allow_inf_nan=False)
    reference_cash_per_share: Decimal = Field(ge=0, allow_inf_nan=False)
    reference_shares_per_share: Decimal = Field(ge=0, allow_inf_nan=False)


class CorporateActionReview(BaseModel):
    """Announcement-backed resolution bound to an exact group of source rows.

    Example:
        review = CorporateActionReview.model_validate_json(review_json)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str = Field(min_length=1)
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    action_id: str = Field(min_length=1)
    shareholder_scope: Literal["ordinary_shareholder"]
    record_date: date
    ex_date: date
    pay_date: date | None
    div_listdate: date | None
    terms: tuple[DistributionTerms, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_timeline(self) -> Self:
        dates = [terms.published_date for terms in self.terms]
        if dates != sorted(set(dates)):
            raise ValueError("review terms must have unique, increasing publication dates")
        grants_right = any(terms.cash_per_share or terms.shares_per_share for terms in self.terms)
        if (self.record_date >= self.ex_date or dates[-1] >= self.ex_date
            or (grants_right and dates[0] >= self.record_date)):
            raise ValueError("review requires an initial plan before registration and revisions before ex date")
        for terms in self.terms:
            for amount, settlement, field in (
                (terms.cash_per_share, self.pay_date, "pay_date"),
                (terms.shares_per_share, self.div_listdate, "div_listdate"),
            ):
                if bool(amount) != (settlement is not None) or (settlement is not None and settlement < self.ex_date):
                    raise ValueError(f"review {field} must follow ex date and match its benefit")
        return self


class CorporateActionResolution(BaseModel):
    """One candidate group's disposition, including unverified and zero-right cases.

    Example:
        resolution = CorporateActionResolution.model_validate_json(stored_resolution)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: str
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    record_date: date
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_row_ids: tuple[str, ...] = Field(min_length=1)
    status: Literal["resolved", "not_entitled", "unresolved"]
    reason: str
    review: CorporateActionReview | None

    @model_validator(mode="after")
    def _validate_review(self) -> Self:
        if (self.status == "unresolved") != (self.review is None):
            raise ValueError("only unresolved candidates may lack an announcement review")
        if self.review is not None:
            if (self.review.candidate_id != self.candidate_id or self.review.source_digest != self.source_digest
                or self.review.record_date != self.record_date):
                raise ValueError("resolution review does not identify its source group")
            no_right = not any(t.cash_per_share or t.shares_per_share for t in self.review.terms)
            if (self.status == "not_entitled") != no_right:
                raise ValueError("not_entitled must match zero ordinary-shareholder rights")
        return self


def resolve_corporate_actions(
    records: Sequence[DividendRecord], reviews: Sequence[CorporateActionReview],
) -> tuple[CorporateActionResolution, ...]:
    """Retain every implemented observation and apply only matching evidenced reviews.

    Example:
        resolutions = resolve_corporate_actions(source_rows, announcement_reviews)
    """
    grouped: dict[str, list[DividendRecord]] = {}
    for row in records:
        if row.div_proc == "实施":
            grouped.setdefault(row.candidate_id, []).append(row)
    by_candidate = {review.candidate_id: review for review in reviews}
    if len(by_candidate) != len(reviews):
        raise ValueError("corporate-action reviews must have unique candidate_id")
    if by_candidate.keys() - grouped.keys():
        raise ValueError("corporate-action review has no matching implemented source group")
    if len({review.action_id for review in reviews}) != len(reviews):
        raise ValueError("corporate-action reviews must have unique action_id")
    resolutions: list[CorporateActionResolution] = []
    for candidate_id, rows in sorted(grouped.items()):
        source_ids = tuple(sorted(row.source_row_id for row in rows))
        digest = sha256("\n".join(source_ids).encode()).hexdigest()
        review = by_candidate.get(candidate_id)
        reason = _unresolved_reason(rows)
        status: Literal["resolved", "not_entitled", "unresolved"] = "unresolved"
        if review is not None:
            if review.source_digest != digest:
                raise ValueError(f"corporate-action review source digest changed: {candidate_id}")
            reason = review.reason
            status = "resolved" if any(t.cash_per_share or t.shares_per_share for t in review.terms) else "not_entitled"
        resolutions.append(CorporateActionResolution(candidate_id=candidate_id, symbol=rows[0].symbol,
            record_date=rows[0].record_date, source_digest=digest, source_row_ids=source_ids,
            status=status, reason=reason, review=review))
    return tuple(resolutions)


def _unresolved_reason(rows: Sequence[DividendRecord]) -> str:
    if any(row.imp_ann_date is None or row.ex_date is None for row in rows):
        return "missing_implementation_dates"
    if any(row.end_date.month not in (3, 6, 9, 12) or row.end_date.day not in (30, 31) for row in rows):
        return "nonstandard_distribution_requires_review"
    if len({row.cash_div_tax for row in rows}) > 1 or len({row.stk_div for row in rows}) > 1:
        return "different_distribution_terms"
    if len({row.imp_ann_date for row in rows}) > 1:
        return "different_announcement_dates"
    if len(rows) > 1:
        return "duplicate_plan_requires_review"
    return "unverified_beneficiary_and_reference"
