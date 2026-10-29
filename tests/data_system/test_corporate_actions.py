# filepath: tests/data_system/test_corporate_actions.py
"""Ordinary source distributions, confirmed corrections and unresolved source defects."""
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal
from itertools import permutations
import json
from pathlib import Path

from tests.fixtures.distributions import verified_distributions

import pytest
from pydantic import TypeAdapter, ValidationError

from src.data_system.corporate_actions import (
    CorporateActionReview, DistributionTerms, DividendRecord, resolve_corporate_actions,
)
from src.utils.datetime_utils import DateTimeUtils


def test_yinyi_keeps_net_allocation_versions_without_inventing_share_credit() -> None:
    cases = json.loads((Path(__file__).parents[1] /
        "fixtures/corporate_actions/confirmed_compensations.json").read_text())
    case = next(case for case in cases if case["records"][0]["symbol"] == "000981")
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    review = CorporateActionReview.model_validate(case["review"])
    result, = resolve_corporate_actions(records, (review,))
    assert result.status == "resolved" and result.review == review and result.source_record is None
    assert records[0].stk_div == Decimal("1.482")
    initial, reference, allocation = review.terms
    assert initial.shares_per_share is reference.shares_per_share is None
    assert initial.reference_price is None and reference.reference_price == Decimal("2.10")
    assert (allocation.shares_numerator, allocation.shares_denominator) == (1431951114, 1165023572)
    assert allocation.available_ts_us == DateTimeUtils.local_time_to_utc_epoch_us(time(), date(2022, 2, 26))
    assert all(term.shares_credit_date is None and term.shares_available_date == date(2022, 2, 25)
        for term in review.terms)
    prefix = CorporateActionReview.model_validate({**case["review"], "terms": case["review"]["terms"][:2]})
    assert prefix.terms == review.terms[:2]


@pytest.mark.parametrize("symbol", ["300116", "600515"])
def test_confirmed_reorganization_keeps_net_rights_and_independent_settlement(symbol: str) -> None:
    cases = json.loads((Path(__file__).parents[1] /
        "fixtures/corporate_actions/confirmed_compensations.json").read_text())
    case = next(case for case in cases if case["records"][0]["symbol"] == symbol)
    rows = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    review = CorporateActionReview.model_validate(case["review"])
    result, = resolve_corporate_actions(rows, (review,))
    assert result.status == "resolved" and result.review == review and result.source_record is None
    assert all(terms.cash_per_share == 0 and terms.shares_per_share != rows[0].stk_div for terms in review.terms)
    assert review.kind == "compensation" and review.pay_date is review.div_listdate is None
    if symbol == "300116":
        terms, = review.terms
        assert terms.shares_per_share == Decimal("0.05")
        assert terms.published_date == date(2020, 3, 25)
        assert review.record_date == date(2020, 3, 31) and review.ex_date is None
        assert terms.shares_credit_date is terms.shares_available_date is None
    else:
        initial, final = review.terms
        assert (initial.shares_numerator, initial.shares_denominator) == (2931936960, 3907592460)
        assert initial.shares_per_share == Decimal(2931936960) / Decimal(3907592460)
        assert initial.reference_price is initial.reference_cash_per_share is None
        assert final.reference_price == Decimal("5.87")
        assert initial.available_ts_us == DateTimeUtils.local_time_to_utc_epoch_us(time(), date(2021, 12, 17))
        assert final.available_ts_us == DateTimeUtils.local_time_to_utc_epoch_us(time(), date(2021, 12, 22))
        assert all(t.shares_credit_date == date(2021, 12, 21)
            and t.shares_available_date == date(2021, 12, 23) for t in review.terms)


@pytest.mark.parametrize("index", range(3))
def test_confirmed_compensation_retains_history_without_an_ex_date(index: int) -> None:
    case = json.loads((Path(__file__).parents[1] /
        "fixtures/corporate_actions/confirmed_compensations.json").read_text())[index]
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    assert all(row.ex_date is None for row in records)
    review = CorporateActionReview.model_validate(case["review"])
    result, = resolve_corporate_actions(records, (review,))
    assert result.status == "resolved" and result.source_record is None
    assert result.review == review and review.kind == "compensation"
    assert review.terms[0].benefit_state == "conditional"
    assert review.terms[-1].benefit_state == "confirmed"
    assert all(t.reference_price is None and t.reference_cash_per_share is None for t in review.terms)
    prefix = CorporateActionReview.model_validate({**review.model_dump(), "terms": review.terms[:1]})
    assert prefix.terms == review.terms[:1]
    if index == 1:
        assert review.terms[1].cash_per_share < review.terms[2].cash_per_share
    if index:
        assert review.terms[0].pay_date is None
        assert review.terms[-1].pay_date < review.terms[-1].published_date


@pytest.mark.parametrize("index", range(69))
def test_confirmed_ordinary_entitlements_bind_all_sources_without_using_total_capital(index: int) -> None:
    case = json.loads((Path(__file__).parents[1] /
        "fixtures/corporate_actions/confirmed_ordinary_entitlements.json").read_text())[index]
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    review = CorporateActionReview.model_validate(case["review"])
    results = resolve_corporate_actions(records, (review,))
    expected_status = "not_entitled" if all(
        terms.cash_per_share == terms.shares_per_share == 0 for terms in review.terms) else "resolved"
    assert {item.candidate_id for item in results} == {record.candidate_id for record in records}
    assert {item.action_id for item in results} == {review.action_id}
    assert {item.status for item in results} == {expected_status}
    assert all(item.review == review and item.source_record is None for item in results)
    if len(review.terms) > 1:
        initial, final = review.terms[0], review.terms[-1]
        assert initial.cash_per_share == final.cash_per_share
        assert initial.shares_per_share == final.shares_per_share
        assert initial.available_ts_us < final.available_ts_us


def test_qualified_reorganization_uses_net_rights_and_separately_published_reference() -> None:
    case = json.loads((Path(__file__).parents[1] / "fixtures/corporate_actions/qualified_reorganization.json").read_text())
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    review = CorporateActionReview.model_validate(case["review"])
    resolution, = resolve_corporate_actions(records, (review,))
    assert resolution.status == "resolved"
    assert records[0].stk_div == Decimal("2.2035714")
    initial, final = review.terms
    assert initial.shares_per_share == final.shares_per_share == Decimal("2.1898786")
    assert initial.reference_price is None
    assert final.reference_price == Decimal("1.80")
    assert initial.available_ts_us < final.available_ts_us
    assert initial.cash_per_share == final.cash_per_share == 0


@pytest.mark.parametrize("index", range(15))
@pytest.mark.parametrize("with_result", [False, True])
def test_confirmed_zero_entitlements_override_company_capital_without_a_reference(index: int, with_result: bool) -> None:
    case = json.loads((Path(__file__).parents[1] /
        "fixtures/corporate_actions/confirmed_zero_entitlements.json").read_text())[index]
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    payload = case["review"]
    if not with_result:
        payload = {**payload, "terms": payload["terms"][:1]}
    review = CorporateActionReview.model_validate(payload)
    results = resolve_corporate_actions(records, (review,))
    assert all(record.stk_div > 0 for record in records)
    assert {result.status for result in results} == {"not_entitled"}
    assert {result.action_id for result in results} == {review.action_id}
    assert all(result.source_record is None and result.review == review for result in results)
    assert review.pay_date is review.div_listdate is None
    assert review.terms[0].reference_cash_per_share is review.terms[0].reference_price is None


def test_zero_entitlement_reference_result_becomes_visible_after_date_only_publication() -> None:
    case = json.loads((Path(__file__).parents[1] /
        "fixtures/corporate_actions/confirmed_zero_entitlements.json").read_text())[0]
    initial = case["review"]["terms"][0]
    case["review"]["terms"] = [initial, {**initial, "published_date": "2025-12-12", "reference_price": "7"}]
    review = CorporateActionReview.model_validate(case["review"])
    assert review.terms[0].reference_price is None
    assert review.terms[1].available_ts_us == DateTimeUtils.local_time_to_utc_epoch_us(time(), date(2025, 12, 13))


@pytest.mark.parametrize("cash,shares", [("1", "0"), ("0", "1"), (None, "0"), ("0", None)])
def test_reference_gap_does_not_discard_positive_or_unknown_rights(
    cash: str | None, shares: str | None,
) -> None:
    case = json.loads((Path(__file__).parents[1] /
        "fixtures/corporate_actions/confirmed_zero_entitlements.json").read_text())[0]
    payload = case["review"]
    payload["terms"] = [{**payload["terms"][0], "cash_per_share": cash, "shares_per_share": shares}]
    payload["pay_date"] = payload["ex_date"] if cash != "0" else None
    payload["div_listdate"] = payload["ex_date"] if shares != "0" else None
    review = CorporateActionReview.model_validate(payload)
    assert review.terms[0].reference_price is review.terms[0].reference_cash_per_share is None


def test_zero_entitlement_confirmation_cannot_be_backdated_before_publication() -> None:
    case = json.loads((Path(__file__).parents[1] /
        "fixtures/corporate_actions/confirmed_zero_entitlements.json").read_text())[0]
    payload = case["review"]
    payload["terms"] = [{**payload["terms"][0], "published_date": payload["ex_date"]}]
    review = CorporateActionReview.model_validate(payload)
    assert review.terms[0].available_ts_us > DateTimeUtils.local_time_to_utc_epoch_us(time(), review.ex_date)


@pytest.mark.parametrize("index, ratio", [(0, "1.226058"), (1, "0.3585165")])
def test_registration_plan_can_precede_the_publication_of_its_allocation_ratio(index: int, ratio: str) -> None:
    case = json.loads((Path(__file__).parents[1] /
        "fixtures/corporate_actions/registration_dependent_allocations.json").read_text())[index]
    payload = case["review"]
    review = CorporateActionReview.model_validate(payload)
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    result, = resolve_corporate_actions(records, (review,))
    assert result.status == "resolved"
    assert review.terms[0].shares_per_share is review.terms[1].shares_per_share is None
    assert review.terms[-1].available_ts_us > review.terms[1].available_ts_us
    assert review.terms[-1].shares_per_share == Decimal(ratio) != records[0].stk_div
    pending = CorporateActionReview.model_validate({**payload, "terms": payload["terms"][:-1]})
    assert resolve_corporate_actions(records, (pending,))[0].status == "resolved"


def test_known_distribution_amount_cannot_be_removed_by_a_later_pending_version() -> None:
    case = json.loads((Path(__file__).parents[1] / "fixtures/corporate_actions/qualified_reorganization.json").read_text())
    case["review"]["terms"][-1]["shares_per_share"] = None
    with pytest.raises(ValidationError, match="known distribution amounts cannot become unknown"):
        CorporateActionReview.model_validate(case["review"])


@pytest.mark.parametrize("publication", [None, "2021-12-31"])
def test_reorganization_keeps_reference_gap_and_actual_publication_time(publication: str | None) -> None:
    case = json.loads((Path(__file__).parents[1] / "fixtures/corporate_actions/qualified_reorganization.json").read_text())
    if publication is None:
        case["review"]["terms"].pop()
    else:
        case["review"]["terms"][-1]["published_date"] = publication
    review = CorporateActionReview.model_validate(case["review"])
    assert review.terms[0].reference_price is None
    if publication is not None:
        assert review.terms[-1].available_ts_us > DateTimeUtils.local_time_to_utc_epoch_us(time(), review.ex_date)


@pytest.mark.parametrize("cash, shares, price", [("0", None, None), (None, "1", None), ("0", "0", "1.8")])
def test_reference_parameters_cannot_be_partial_or_mix_with_absolute_price(
    cash: str | None, shares: str | None, price: str | None,
) -> None:
    with pytest.raises(ValidationError, match="reference requires a complete formula pair or an absolute price"):
        DistributionTerms(published_date=date(2021, 12, 27), cash_per_share=Decimal(0), shares_per_share=Decimal(0),
            reference_cash_per_share=cash, reference_shares_per_share=shares, reference_price=price)


@pytest.mark.parametrize("index", range(5))
def test_verified_source_groups_resolve_once_with_all_observations(index: int) -> None:
    case = verified_distributions()[index]
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    review = CorporateActionReview.model_validate(case["review"])
    resolutions = resolve_corporate_actions(records, (review,))
    assert len(resolutions) == 1
    result = resolutions[0]
    assert result.review == review
    assert len(records) == 2
    assert result.action_id == review.action_id
    assert result.status == ("not_entitled" if records[0].symbol == "002157" else "resolved")


@pytest.mark.parametrize("index", (1, 2, 3, 4))
def test_ambiguous_unreviewed_groups_never_grant_guessed_rights(index: int) -> None:
    case = verified_distributions()[index]
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    result, = resolve_corporate_actions(records, ())
    assert result.status == "unresolved"
    assert result.review is None
    assert result.action_id is None


@pytest.fixture
def ordinary_source() -> DividendRecord:
    return DividendRecord.model_validate_json(
        (Path(__file__).parents[1] / "fixtures/corporate_actions/ordinary_dividend.json").read_bytes())


def test_complete_ordinary_source_is_executable_without_an_announcement(ordinary_source: DividendRecord) -> None:
    result, = resolve_corporate_actions((ordinary_source,), ())
    assert result.status == "resolved"
    assert result.reason == "ordinary_dividend_source"
    assert result.source_record == ordinary_source
    assert result.review is None
    assert result.action_id == "dividend:600000.SH/2026-06-30/2026-07-17:ordinary_shareholder"


def test_base_capital_remains_source_observation_without_changing_entitlements(ordinary_source: DividendRecord) -> None:
    record = DividendRecord.model_validate({
        **ordinary_source.model_dump(), "base_date": "2026-06-30", "base_share": 12345678.9,
    })
    result, = resolve_corporate_actions((record,), ())
    original, = resolve_corporate_actions((ordinary_source,), ())
    assert result.status == original.status == "resolved"
    assert result.action_id == original.action_id
    assert result.source_record == record
    assert result.source_record.base_share == 12345678.9
    assert result.source_record.base_date == date(2026, 6, 30)
    assert result.source_record.cash_div_tax == original.source_record.cash_div_tax
    assert result.source_record.stk_div == original.source_record.stk_div


@pytest.mark.parametrize(("changes", "reason"), [
    ({"cash_div_tax": None}, "missing_distribution_terms"),
    ({"stk_div": None}, "missing_distribution_terms"),
    ({"imp_ann_date": None}, "missing_implementation_dates"),
    ({"ex_date": None}, "missing_implementation_dates"),
    ({"imp_ann_date": "2026-07-17"}, "unsupported_source_timeline"),
    ({"imp_ann_date": "2026-07-21"}, "unsupported_source_timeline"),
    ({"ex_date": "2026-07-17"}, "unsupported_source_timeline"),
    ({"pay_date": None}, "invalid_source_settlement_dates"),
    ({"pay_date": "2026-07-17"}, "invalid_source_settlement_dates"),
    ({"div_listdate": None}, "invalid_source_settlement_dates"),
    ({"end_date": "2026-06-29"}, "nonstandard_distribution_requires_review"),
])
def test_source_gaps_and_late_terms_do_not_become_executable(
    ordinary_source: DividendRecord, changes: dict[str, object], reason: str,
) -> None:
    row = DividendRecord.model_validate({**ordinary_source.model_dump(), **changes})
    result, = resolve_corporate_actions((row,), ())
    assert result.status == "unresolved"
    assert result.reason == reason
    assert result.source_record is None
    assert result.action_id is None


def test_equivalent_observations_resolve_once_regardless_of_order(ordinary_source: DividendRecord) -> None:
    later_plan = ordinary_source.model_copy(update={"ann_date": date(2026, 7, 13)})
    for observations in permutations((ordinary_source, ordinary_source, later_plan)):
        result, = resolve_corporate_actions(observations, ())
        assert result.status == "resolved"
        assert result.source_record == later_plan
        assert result.action_id == "dividend:600000.SH/2026-06-30/2026-07-17:ordinary_shareholder"
        assert result.source_record.cash_div_tax == Decimal("1")
        assert result.source_record.stk_div == Decimal("0.5")


def test_distinct_source_candidates_are_not_added_or_chosen(ordinary_source: DividendRecord) -> None:
    second = DividendRecord.model_validate({**ordinary_source.model_dump(), "end_date": "2025-12-31"})
    separate = resolve_corporate_actions((ordinary_source, second), ())
    assert len(separate) == 2
    assert {item.reason for item in separate} == {"same_registration_candidates_require_review"}
    assert all(item.status == "unresolved" for item in separate)


def test_historical_duplicate_observations_do_not_require_a_review() -> None:
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(verified_distributions()[0]["records"])
    result, = resolve_corporate_actions(records, ())
    assert result.status == "resolved"
    assert result.source_record == records[1]
    assert result.source_record.cash_div_tax == Decimal("0.247")
    assert result.source_record.stk_div == Decimal("1.0")


@pytest.mark.parametrize("cash", ("0", "1"))
def test_missing_amount_does_not_dispute_a_complete_observation(
    ordinary_source: DividendRecord, cash: str,
) -> None:
    complete = ordinary_source.model_copy(update={"cash_div_tax": Decimal(cash),
        "pay_date": ordinary_source.pay_date if Decimal(cash) else None})
    missing = complete.model_copy(update={"cash_div_tax": None, "imp_ann_date": date(2026, 7, 15)})
    for rows in ((missing, complete), (complete, missing)):
        result, = resolve_corporate_actions(rows, ())
        assert result.status == "resolved"
        assert result.source_record == complete
        assert result.source_record.imp_ann_date == date(2026, 7, 16)
    complementary = complete.model_copy(update={"stk_div": None})
    result, = resolve_corporate_actions((missing, complementary), ())
    if cash == "0":
        assert result.status == "resolved"
        assert result.source_record == missing
        assert result.source_record.cash_div_tax is None
    else:
        assert result.status == "unresolved"
        assert result.reason == "missing_distribution_terms"
        assert result.source_record is None


def test_equivalent_terms_use_the_later_complete_publication(ordinary_source: DividendRecord) -> None:
    earlier = ordinary_source.model_copy(update={"imp_ann_date": date(2026, 7, 15)})
    for rows in ((earlier, ordinary_source), (ordinary_source, earlier)):
        result, = resolve_corporate_actions(rows, ())
        assert result.status == "resolved"
        assert result.source_record == ordinary_source
    too_late = ordinary_source.model_copy(update={"imp_ann_date": ordinary_source.record_date})
    result, = resolve_corporate_actions((ordinary_source, too_late), ())
    assert result.status == "unresolved"
    assert result.reason == "unsupported_source_timeline"


@pytest.mark.parametrize("older_publication", [None, date(2026, 7, 14)])
def test_later_implementation_supersedes_an_older_plan_without_backdating(
    ordinary_source: DividendRecord, older_publication: date | None,
) -> None:
    older = ordinary_source.model_copy(update={"cash_div_tax": Decimal("0.81"),
        "ann_date": date(2026, 7, 1), "imp_ann_date": older_publication})
    for rows in permutations((older, ordinary_source)):
        result, = resolve_corporate_actions(rows, ())
        assert result.status == "resolved"
        assert result.source_record == ordinary_source
        assert result.source_record.cash_div_tax == Decimal("1")
        assert result.source_record.imp_ann_date == date(2026, 7, 16)


def test_later_undated_observation_cannot_override_a_known_implementation(ordinary_source: DividendRecord) -> None:
    unknown = ordinary_source.model_copy(update={"cash_div_tax": Decimal("2"),
        "ann_date": date(2026, 7, 17), "imp_ann_date": None})
    result, = resolve_corporate_actions((ordinary_source, unknown), ())
    assert result.status == "unresolved"
    assert result.reason == "missing_implementation_dates"


def test_newer_plan_date_does_not_resolve_a_same_publication_conflict(ordinary_source: DividendRecord) -> None:
    changed = ordinary_source.model_copy(update={"cash_div_tax": Decimal("2"), "ann_date": date(2026, 7, 16)})
    result, = resolve_corporate_actions((ordinary_source, changed), ())
    assert result.status == "unresolved"
    assert result.reason == "different_distribution_terms"


@pytest.mark.parametrize(("changes", "reason"), [
    ({"cash_div_tax": "2"}, "different_distribution_terms"),
    ({"stk_div": "0.4"}, "different_distribution_terms"),
    ({"ex_date": "2026-07-21"}, "different_phase_dates"),
    ({"pay_date": "2026-07-22"}, "different_phase_dates"),
    ({"div_listdate": "2026-07-22"}, "different_phase_dates"),
])
def test_conflicting_economic_terms_are_not_merged(
    ordinary_source: DividendRecord, changes: dict[str, object], reason: str,
) -> None:
    changed = DividendRecord.model_validate({**ordinary_source.model_dump(), **changes})
    for rows in ((ordinary_source, changed), (changed, ordinary_source)):
        result, = resolve_corporate_actions(rows, ())
        assert result.status == "unresolved"
        assert result.reason == reason
        assert result.source_record is None


def test_cash_distribution_does_not_require_a_quarter_end_period(ordinary_source: DividendRecord) -> None:
    cash_only = ordinary_source.model_copy(update={"end_date": date(2026, 6, 29),
        "stk_div": Decimal(0), "div_listdate": None})
    result, = resolve_corporate_actions((cash_only, cash_only), ())
    assert result.status == "resolved"
    assert result.source_record == cash_only
    assert result.action_id == "dividend:600000.SH/2026-06-29/2026-07-17:ordinary_shareholder"


@pytest.mark.parametrize("null_cash", [False, True])
def test_confirmed_correction_overrides_even_a_complete_ordinary_source(
    ordinary_source: DividendRecord, null_cash: bool,
) -> None:
    review = CorporateActionReview(candidate_id=ordinary_source.candidate_id,
        action_id="confirmed-correction",
        shareholder_scope="ordinary_shareholder", record_date=ordinary_source.record_date,
        ex_date=ordinary_source.ex_date, pay_date=ordinary_source.pay_date, div_listdate=ordinary_source.div_listdate,
        terms=[dict(published_date="2026-07-16", cash_per_share="0.8", shares_per_share="0.5",
            reference_cash_per_share="0.7", reference_shares_per_share="0.5"),
            dict(published_date="2026-07-18", cash_per_share="0.9", shares_per_share="0.5",
            reference_cash_per_share="0.85", reference_shares_per_share="0.5")],
        evidence_refs=("fixture://confirmed-correction",), reason="confirmed_original_and_revision")
    source = ordinary_source.model_copy(update={"cash_div_tax": None, "pay_date": None}) if null_cash else ordinary_source
    result, = resolve_corporate_actions((source, source), (review,))
    assert result.source_record is None
    assert result.review == review
    assert result.action_id == review.action_id
    assert str(result.review.terms[0].cash_per_share) == "0.8"
    assert str(result.review.terms[1].cash_per_share) == "0.9"


def test_explicit_zero_rights_and_unimplemented_plan_are_distinct(ordinary_source: DividendRecord) -> None:
    row = DividendRecord.model_validate({**ordinary_source.model_dump(), "cash_div_tax": "0", "stk_div": "0",
        "pay_date": None, "div_listdate": None})
    result, = resolve_corporate_actions((row,), ())
    assert result.status == "not_entitled"
    assert result.source_record == row
    assert resolve_corporate_actions((row.model_copy(update={"div_proc": "预案"}),), ()) == ()


def test_original_and_revised_terms_remain_distinct_versions() -> None:
    review = CorporateActionReview.model_validate(verified_distributions()[4]["review"])
    assert review.terms[0].published_date.isoformat() == "2025-06-30"
    assert str(review.terms[0].cash_per_share) == "0.2816137"
    assert review.terms[1].published_date.isoformat() == "2025-07-05"
    assert str(review.terms[1].cash_per_share) == "0.2814904"
    assert review.terms[0].published_date < review.record_date < review.terms[1].published_date < review.ex_date


def test_review_requires_unique_and_matching_candidates() -> None:
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(verified_distributions()[0]["records"])
    review = CorporateActionReview.model_validate(verified_distributions()[0]["review"])
    unrelated = review.model_copy(update={"candidate_id": "unrelated-candidate"})
    with pytest.raises(ValueError, match="no matching implemented source"):
        resolve_corporate_actions(records, (unrelated,))
    with pytest.raises(ValueError, match="unique candidate_id"):
        resolve_corporate_actions(records, (review, review))


def test_ordinary_stock_distribution_keeps_the_original_null_cash() -> None:
    source = deepcopy(verified_distributions()[0]["records"][0])
    source.update(cash_div_tax=None, pay_date=None)
    row = DividendRecord.model_validate(source)
    earlier = row.model_copy(update={"imp_ann_date": row.imp_ann_date.replace(day=row.imp_ann_date.day - 1)})
    for rows in ((earlier, row, row), (row, earlier)):
        result, = resolve_corporate_actions(rows, ())
        assert result.status == "resolved"
        assert result.source_record == row
        assert result.source_record.cash_div_tax is None
        assert result.source_record.pay_date is None
        assert result.source_record.imp_ann_date == row.imp_ann_date
        assert result.action_id == f"dividend:{row.candidate_id}:ordinary_shareholder"
    assert result.review is None


@pytest.mark.parametrize(("changes", "reason"), [
    ({"pay_date": "2026-07-21"}, "missing_distribution_terms"),
    ({"stk_div": None}, "missing_distribution_terms"),
    ({"stk_div": "0", "div_listdate": None}, "missing_distribution_terms"),
    ({"div_listdate": None}, "invalid_source_settlement_dates"),
    ({"div_listdate": "2026-07-17"}, "invalid_source_settlement_dates"),
    ({"imp_ann_date": "2026-07-17"}, "unsupported_source_timeline"),
    ({"end_date": "2026-06-29"}, "nonstandard_distribution_requires_review"),
])
def test_null_cash_does_not_override_other_distribution_gaps(
    ordinary_source: DividendRecord, changes: dict[str, object], reason: str,
) -> None:
    row = DividendRecord.model_validate({**ordinary_source.model_dump(), "cash_div_tax": None,
        "pay_date": None, **changes})
    result, = resolve_corporate_actions((row,), ())
    assert result.status == "unresolved"
    assert result.reason == reason
    assert result.source_record is None


def test_explicit_zero_observation_precedes_a_later_null_observation(ordinary_source: DividendRecord) -> None:
    explicit = ordinary_source.model_copy(update={"cash_div_tax": Decimal(0), "pay_date": None,
        "imp_ann_date": date(2026, 7, 15)})
    missing = explicit.model_copy(update={"cash_div_tax": None, "imp_ann_date": date(2026, 7, 16)})
    result, = resolve_corporate_actions((missing, explicit), ())
    assert result.status == "resolved"
    assert result.source_record == explicit


def test_null_cash_does_not_override_explicit_cash_in_an_incomplete_observation(ordinary_source: DividendRecord) -> None:
    stock = ordinary_source.model_copy(update={"cash_div_tax": None, "pay_date": None})
    cash = stock.model_copy(update={"cash_div_tax": Decimal(1), "stk_div": None})
    result, = resolve_corporate_actions((stock, cash), ())
    assert result.status == "unresolved"
    assert result.reason == "missing_distribution_terms"


def test_review_requires_explicit_null_and_preserves_late_revision_visibility() -> None:
    payload = deepcopy(verified_distributions()[4]["review"])
    del payload["terms"][0]["reference_cash_per_share"]
    with pytest.raises(ValidationError, match="reference_cash_per_share"):
        CorporateActionReview.model_validate(payload)
    payload = deepcopy(verified_distributions()[4]["review"])
    payload["terms"][-1]["published_date"] = payload["ex_date"]
    review = CorporateActionReview.model_validate(payload)
    assert review.terms[-1].available_ts_us > DateTimeUtils.local_time_to_utc_epoch_us(time(), review.ex_date)
    assert review.terms[0].published_date < review.terms[-1].published_date


def test_one_announced_event_can_bind_distinct_source_candidates_without_double_counting() -> None:
    case = verified_distributions()[1]
    first = DividendRecord.model_validate(case["records"][0])
    second = first.model_copy(update={"end_date": first.end_date.replace(month=3)})
    review = CorporateActionReview.model_validate(case["review"]).model_copy(update={
        "related_sources": (second.candidate_id,),
    })
    resolved = resolve_corporate_actions((first, second), (review,))
    assert len(resolved) == 2
    assert all(item.review == review and item.status == "resolved" for item in resolved)
    assert len({item.review.action_id for item in resolved}) == 1
    with pytest.raises(ValueError, match="no matching implemented source"):
        resolve_corporate_actions((first,), (review,))


def test_reference_only_revision_can_become_visible_after_ex_at_its_verified_time() -> None:
    payload = deepcopy(verified_distributions()[0]["review"])
    original = payload["terms"][0]
    stamp = int(datetime.fromisoformat(payload["ex_date"] + "T07:41:10+08:00").timestamp()) * 1_000_000
    payload["terms"].append({**original, "published_date": payload["ex_date"],
        "published_ts_us": stamp, "reference_cash_per_share": "0.039943"})
    review = CorporateActionReview.model_validate(payload)
    assert review.terms[-1].available_ts_us == stamp
    assert review.terms[-1].cash_per_share == review.terms[0].cash_per_share
    payload["terms"][-1]["cash_per_share"] = "0.041"
    changed = CorporateActionReview.model_validate(payload)
    assert changed.terms[0].cash_per_share != changed.terms[-1].cash_per_share
    assert changed.terms[-1].available_ts_us == stamp


def test_exact_publication_time_must_match_its_declared_local_date() -> None:
    payload = deepcopy(verified_distributions()[0]["review"])
    payload["terms"][0]["published_ts_us"] = int(datetime.fromisoformat(
        payload["record_date"] + "T07:41:10+08:00").timestamp()) * 1_000_000
    with pytest.raises(ValidationError, match="publication.*date"):
        CorporateActionReview.model_validate(payload)


def test_independent_announcement_retains_its_identity_when_dividend_source_arrives() -> None:
    payload = deepcopy(verified_distributions()[0]["review"])
    original = DividendRecord.model_validate(verified_distributions()[0]["records"][0])
    url = "https://static.cninfo.com.cn/finalpage/2018-03-30/1204547578.PDF"
    payload.update(candidate_id="cninfo:1204547578",
        action_id="cninfo:1204547578:ordinary_shareholder", evidence_refs=(url,),
        announcement_source={"source": "cninfo", "announcement_id": "1204547578", "symbol": original.symbol,
            "title": "2017年年度权益分派实施公告", "published_date": payload["terms"][0]["published_date"], "url": url})
    review = CorporateActionReview.model_validate(payload)
    standalone, = resolve_corporate_actions((), (review,))
    assert standalone.status == "resolved"
    assert standalone.symbol == original.symbol
    assert standalone.action_id == review.action_id
    unlinked = resolve_corporate_actions((original,), (review,))
    source_result = next(row for row in unlinked if row.candidate_id == original.candidate_id)
    assert source_result.status == "unresolved"
    assert source_result.reason == "same_registration_candidates_require_review"
    payload["related_sources"] = [original.candidate_id]
    linked = CorporateActionReview.model_validate(payload)
    rows = resolve_corporate_actions((original,), (linked,))
    assert {row.candidate_id for row in rows} == {standalone.candidate_id, original.candidate_id}
    assert all(row.review == linked for row in rows)
    assert len({row.review.action_id for row in rows}) == 1
    assert next(row for row in rows if row.candidate_id == original.candidate_id).action_id == standalone.action_id


def test_independent_announcement_cannot_invent_its_native_identity_or_evidence() -> None:
    payload = deepcopy(verified_distributions()[0]["review"])
    payload["announcement_source"] = {"source": "cninfo", "announcement_id": "1204547578", "symbol": "002745",
        "title": "权益分派实施公告", "published_date": "2018-03-30", "url": "https://static.cninfo.com.cn/example.PDF"}
    with pytest.raises(ValidationError, match="announcement source"):
        CorporateActionReview.model_validate(payload)
