# filepath: tests/data_system/test_corporate_actions.py
"""Announcement-backed regressions for source duplication and incomplete facts."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from src.data_system.corporate_actions import (
    CorporateActionReview, DividendRecord, resolve_corporate_actions,
)


def _read_cases() -> list[dict[str, object]]:
    return json.loads((Path(__file__).parents[1] / "fixtures/corporate_actions/verified_distributions.json").read_text())


@pytest.mark.parametrize("index", range(5))
def test_verified_source_groups_resolve_once_with_all_observations(index: int) -> None:
    case = _read_cases()[index]
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    review = CorporateActionReview.model_validate(case["review"])
    resolutions = resolve_corporate_actions(records, (review,))
    assert len(resolutions) == 1
    result = resolutions[0]
    assert result.review == review
    assert len(result.source_row_ids) == len(records) == 2
    assert result.source_digest == sha256("\n".join(sorted(row.source_row_id for row in records)).encode()).hexdigest()
    assert result.status == ("not_entitled" if records[0].symbol == "002157" else "resolved")


@pytest.mark.parametrize("index", range(5))
def test_unreviewed_rows_never_grant_guessed_rights(index: int) -> None:
    case = _read_cases()[index]
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    result, = resolve_corporate_actions(records, ())
    assert result.status == "unresolved"
    assert result.review is None
    assert result.source_row_ids == tuple(sorted(row.source_row_id for row in records))


def test_original_and_revised_terms_remain_distinct_versions() -> None:
    review = CorporateActionReview.model_validate(_read_cases()[4]["review"])
    assert review.terms[0].published_date.isoformat() == "2025-06-30"
    assert str(review.terms[0].cash_per_share) == "0.2816137"
    assert review.terms[1].published_date.isoformat() == "2025-07-05"
    assert str(review.terms[1].cash_per_share) == "0.2814904"
    assert review.terms[0].published_date < review.record_date < review.terms[1].published_date < review.ex_date


def test_review_cannot_be_silently_applied_to_a_changed_snapshot() -> None:
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(_read_cases()[0]["records"])
    review = CorporateActionReview.model_validate(_read_cases()[0]["review"])
    changed = (records[0].model_copy(update={"source_row_id": "0" * 64}), records[1])
    with pytest.raises(ValueError, match="source digest changed"):
        resolve_corporate_actions(changed, (review,))
    with pytest.raises(ValueError, match="unique candidate_id"):
        resolve_corporate_actions(records, (review, review))


def test_null_cash_cannot_be_inferred_as_zero_from_a_stock_ratio() -> None:
    source = deepcopy(_read_cases()[0]["records"][0])
    source.update(cash_div_tax=None, pay_date=None)
    result, = resolve_corporate_actions((DividendRecord.model_validate(source),), ())
    assert result.status == "unresolved"
    assert result.review is None


def test_review_requires_explicit_ex_reference_and_pre_ex_revision_dates() -> None:
    payload = deepcopy(_read_cases()[4]["review"])
    del payload["terms"][0]["reference_cash_per_share"]
    with pytest.raises(ValidationError, match="reference_cash_per_share"):
        CorporateActionReview.model_validate(payload)
    payload = deepcopy(_read_cases()[4]["review"])
    payload["terms"][-1]["published_date"] = payload["ex_date"]
    with pytest.raises(ValidationError, match="revisions before ex date"):
        CorporateActionReview.model_validate(payload)


def test_one_announced_event_can_bind_distinct_source_candidates_without_double_counting() -> None:
    case = _read_cases()[1]
    first = DividendRecord.model_validate(case["records"][0])
    second = first.model_copy(update={"end_date": first.end_date.replace(month=3), "source_row_id": "3" * 64})
    review = CorporateActionReview.model_validate(case["review"]).model_copy(update={
        "source_digest": sha256(first.source_row_id.encode()).hexdigest(),
        "related_sources": {second.candidate_id: sha256(second.source_row_id.encode()).hexdigest()},
    })
    resolved = resolve_corporate_actions((first, second), (review,))
    assert len(resolved) == 2
    assert all(item.review == review and item.status == "resolved" for item in resolved)
    assert len({item.review.action_id for item in resolved}) == 1
    changed = second.model_copy(update={"source_row_id": "4" * 64})
    with pytest.raises(ValueError, match="source digest changed"):
        resolve_corporate_actions((first, changed), (review,))
