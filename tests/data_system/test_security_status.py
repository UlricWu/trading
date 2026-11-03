# filepath: tests/data_system/test_security_status.py
"""Reviewed security states retain source identity and historical visibility."""

from datetime import date, time

import pytest

from src.data_system.security_status import SecurityStatusReviews
from src.utils.datetime_utils import DateTimeUtils


@pytest.fixture
def status_reviews() -> dict[str, object]:
    return {"version": "v1",
        "documents": [{"announcement_id": "1", "symbol": "600000", "title": "停牌公告",
            "published_date": "2026-07-16", "url": "https://example.org/1.pdf",
            "fetched_at": "2026-10-17T00:00:00Z"}], "reviews": [{
        "ts_code": "600000.SH", "trade_date": "2026-07-17", "listing_status": "listed",
        "trading_status": "suspended", "available_ts_us": DateTimeUtils.local_time_to_utc_epoch_us(
            time(), date(2026, 7, 17)), "evidence_refs": ["https://example.org/1.pdf"],
        "reason": "A-share suspension"}]}


@pytest.mark.parametrize("digest", [None, "a" * 64])
def test_security_status_accepts_source_references_without_requiring_a_digest(
    status_reviews: dict[str, object], digest: str | None,
) -> None:
    if digest is not None:
        status_reviews["documents"][0]["sha256"] = digest
    result = SecurityStatusReviews.model_validate(status_reviews)
    assert result.documents[0].sha256 == digest
    assert result.reviews[0].evidence_refs == ("https://example.org/1.pdf",)
    assert result.reviews[0].trading_status == "suspended"
    assert set(result.model_dump()) == {"version", "documents", "reviews"}


@pytest.mark.parametrize("mutation, message", [
    ("security", "does not identify its security"),
    ("reference", "does not identify its security"),
    ("announcement_id", "unique announcement identities"),
    ("url", "unique announcement identities"),
    ("duplicate_status", "unique by security"),
    ("publication", "before its evidence"),
    ("available_time", "before the account day starts"),
])
def test_status_evidence_requires_unambiguous_identity_and_public_time(
    status_reviews: dict[str, object], mutation: str, message: str,
) -> None:
    if mutation == "security":
        status_reviews["documents"][0]["symbol"] = "000001"
    elif mutation == "reference":
        status_reviews["reviews"][0]["evidence_refs"] = ["https://example.org/unknown.pdf"]
    elif mutation in {"announcement_id", "url"}:
        document = {**status_reviews["documents"][0]}
        if mutation == "announcement_id":
            document["url"] = "https://example.org/2.pdf"
        else:
            document["announcement_id"] = "2"
        status_reviews["documents"].append(document)
    elif mutation == "duplicate_status":
        status_reviews["reviews"].append({**status_reviews["reviews"][0]})
    elif mutation == "publication":
        status_reviews["documents"][0]["published_date"] = "2026-07-17"
    else:
        status_reviews["reviews"][0]["available_ts_us"] += 1
    with pytest.raises(ValueError, match=message):
        SecurityStatusReviews.model_validate(status_reviews)
