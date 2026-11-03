# filepath: src/data_system/security_status.py
"""Dated, evidence-bound listing and trading status supplements."""

from datetime import date, time, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.data_system.announcement_evidence import AnnouncementFile
from src.utils.datetime_utils import DateTimeUtils


class SecurityStatus(BaseModel):
    """Confirm independent states for one account day using already public evidence.

    None leaves that dimension unconfirmed; it is not a claim of normal trading.

    Example:
        status = SecurityStatus(ts_code="601238.SH", trade_date="2026-09-15",
            listing_status="listed", trading_status="suspended", available_ts_us=1789401600000000,
            evidence_refs=("https://example.org/notice.pdf",),
            reason="A-share trading remains suspended")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    ts_code: str = Field(pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    trade_date: date
    listing_status: Literal["pre_listing", "listed", "delisted"] | None = None
    trading_status: Literal["suspended", "trading"] | None = None
    available_ts_us: int = Field(ge=0)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @property
    def symbol(self) -> str:
        """Return the source-identified security's six-digit account symbol.

        Example:
            print(status.symbol)
        """
        return self.ts_code.split(".")[0]

    @model_validator(mode="after")
    def _validate_states(self) -> Self:
        if self.listing_status is None and self.trading_status is None:
            raise ValueError("security status must confirm at least one dimension")
        if self.trading_status is not None and self.listing_status != "listed":
            raise ValueError("trading status requires a confirmed listed security")
        start_us = DateTimeUtils.local_time_to_utc_epoch_us(time(), self.trade_date)
        if self.available_ts_us > start_us:
            raise ValueError("full-day security status must be public before the account day starts")
        return self


class SecurityStatusReviews(BaseModel):
    """Associate reviewed states with their original disclosures and public times.

    Example:
        inputs = SecurityStatusReviews.model_validate_json(review_path.read_bytes())
        print(len(inputs.reviews))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["v1"]
    documents: tuple[AnnouncementFile, ...]
    reviews: tuple[SecurityStatus, ...]

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        identities = [review.symbol for review in self.reviews]
        if len(identities) != len(set(identities)):
            raise ValueError("security status must be unique by security within a daily partition")
        documents = {doc.url: doc for doc in self.documents}
        ids = [doc.announcement_id for doc in self.documents]
        if len(ids) != len(set(ids)) or len(documents) != len(self.documents):
            raise ValueError("security status evidence must have unique announcement identities")
        for review in self.reviews:
            for ref in review.evidence_refs:
                if ref not in documents or documents[ref].symbol != review.symbol:
                    raise ValueError(f"security status evidence does not identify its security: {review.ts_code}")
                visible_date = documents[ref].published_date + timedelta(days=1)
                visible_us = DateTimeUtils.local_time_to_utc_epoch_us(time(), visible_date)
                if review.available_ts_us < visible_us:
                    raise ValueError(f"security status is available before its evidence: {review.ts_code}")
        return self
