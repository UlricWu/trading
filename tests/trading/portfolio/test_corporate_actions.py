# filepath: tests/trading/portfolio/test_corporate_actions.py
"""Reference provenance remains explicit in an account's resumable distribution."""

from decimal import Decimal

import pytest

from src.trading.portfolio.corporate_actions import (
    Distribution, DistributionEntitlement, credit_compensation_shares, recognize_distribution,
)
from src.trading.portfolio.state import PortfolioState


def test_ordinary_formula_snapshot_cannot_claim_different_reference_parameters() -> None:
    values = dict(action_id="dividend:ordinary", symbol="600000", source_ref="corporate_actions/v1/2026-07-17",
        announced_ts_us=1, record_ts_us=2, ex_ts_us=3, pay_ts_us=4, shares_available_ts_us=4,
        cash_per_share="1", shares_per_share="0.5", reference_cash_per_share="1",
        reference_shares_per_share="0.5", reference_basis="ordinary_formula_assumption")
    source = Distribution.model_validate(values)
    assert Distribution.model_validate_json(source.model_dump_json()) == source
    with pytest.raises(ValueError, match="ordinary reference assumption"):
        Distribution.model_validate({**values, "reference_cash_per_share": "0.9"})
    with pytest.raises(ValueError, match="ordinary reference assumption"):
        Distribution.model_validate({**values, "reference_shares_per_share": "0.4"})
    confirmed = Distribution.model_validate({**values, "reference_cash_per_share": "0.9", "reference_basis": "announcement"})
    assert confirmed.cash_per_share != confirmed.reference_cash_per_share


@pytest.mark.parametrize("benefit", ["cash", "shares"])
def test_pending_entitlement_preserves_unknown_and_cannot_claim_recognition(benefit: str) -> None:
    action = Distribution(action_id="pending/600000", symbol="600000", source_ref="fixture://pending",
        announced_ts_us=1, record_ts_us=2, ex_ts_us=3, pay_ts_us=4 if benefit == "cash" else None,
        shares_available_ts_us=4 if benefit == "shares" else None,
        cash_per_share=None if benefit == "cash" else "0", shares_per_share=None if benefit == "shares" else "0",
        reference_cash_per_share=None, reference_shares_per_share=None, reference_price="6")
    item = DistributionEntitlement(distribution=action, eligible_qty=100, recognition_issue="pending_distribution_terms")
    assert DistributionEntitlement.model_validate_json(item.model_dump_json()) == item
    with pytest.raises(ValueError, match="not yet known"):
        _ = item.cash_amount if benefit == "cash" else item.shares_qty
    with pytest.raises(ValueError, match="recognized distribution amounts must be known"):
        DistributionEntitlement(distribution=action, eligible_qty=100, recognized=True)


@pytest.mark.parametrize("actual, expected", [(None, 335), (336, 336)])
def test_compensation_uses_exact_formula_and_declared_account_rounding(actual: int | None, expected: int) -> None:
    action = Distribution(action_id="compensation/600828", symbol="600828", source_ref="fixture://600828",
        kind="compensation", announced_ts_us=4, record_ts_us=2, ex_ts_us=None,
        shares_credit_ts_us=6, shares_available_ts_us=7, cash_per_share=0, shares_per_share="0.0335",
        shares_numerator=8479892, shares_denominator=252967347,
        reference_cash_per_share=None, reference_shares_per_share=None)
    entitlement = DistributionEntitlement(distribution=action, eligible_qty=10000, actual_shares_qty=actual)
    account = PortfolioState(initial_cash=Decimal(0), cash=Decimal(0), positions={"600828": 10000},
        avg_cost={"600828": Decimal(10)}, distribution_entitlements={action.action_id: entitlement})
    recognized = recognize_distribution(account, entitlement, ts_us=4)
    assert recognized.shares_delta == 0
    assert recognized.shares_receivable_delta == expected
    assert recognized.allocation_basis == ("account_result" if actual is not None else "floor_assumption")
    assert recognized.fractional_shares == Decimal(84798920000) / Decimal(252967347) - expected
    assert account.positions == {"600828": 10000}
    assert account.t1_sellable("600828") == 10000
    credit = credit_compensation_shares(account, account.distribution_entitlements[action.action_id], ts_us=6)
    assert credit.shares_delta == expected and credit.shares_receivable_delta == -expected
    assert account.positions == {"600828": 10000 + expected}
    assert account.t1_sellable("600828") == 10000
    assert account.cash == 0
    assert DistributionEntitlement.model_validate_json(account.distribution_entitlements[action.action_id].model_dump_json()) == account.distribution_entitlements[action.action_id]


def test_integer_formula_does_not_round_a_whole_allocation_down() -> None:
    action = Distribution(action_id="compensation/all", symbol="600828", source_ref="fixture://whole",
        kind="compensation", announced_ts_us=4, record_ts_us=2, ex_ts_us=None,
        cash_per_share=0, shares_per_share="0.0335", shares_numerator=8479892, shares_denominator=252967347,
        reference_cash_per_share=None, reference_shares_per_share=None)
    assert DistributionEntitlement(distribution=action, eligible_qty=252967347).shares_qty == 8479892


@pytest.mark.parametrize("credit_time", [None, 3, 4, 5])
def test_compensation_listing_and_account_credit_are_independent_facts(credit_time: int | None) -> None:
    action = Distribution(action_id="compensation/listing", symbol="A", source_ref="fixture://listing",
        kind="compensation", announced_ts_us=1, record_ts_us=2, ex_ts_us=4,
        cash_per_share=0, shares_per_share=1, reference_cash_per_share=None, reference_shares_per_share=None,
        shares_credit_ts_us=credit_time, shares_available_ts_us=4)
    account = PortfolioState(initial_cash=Decimal(0), cash=Decimal(0), positions={"A": 100})
    entitlement = DistributionEntitlement(distribution=action, eligible_qty=100)
    recognize_distribution(account, entitlement, ts_us=4)
    saved = account.distribution_entitlements[action.action_id]
    assert saved.recognized and not saved.shares_credited and not saved.shares_released
    assert saved.shares_qty == 100
    assert account.positions == {"A": 100}
    assert Distribution.model_validate_json(action.model_dump_json()) == action


@pytest.mark.parametrize("state", ["conditional", "cancelled"])
def test_uncertain_or_cancelled_compensation_cannot_be_a_confirmed_asset(state: str) -> None:
    action = Distribution(action_id="compensation/conditional", symbol="A", source_ref="fixture://conditional",
        kind="compensation", benefit_state=state, announced_ts_us=1, record_ts_us=2, ex_ts_us=None,
        cash_per_share=1, shares_per_share=0, reference_cash_per_share=None, reference_shares_per_share=None)
    with pytest.raises(ValueError, match="confirmed benefits"):
        DistributionEntitlement(distribution=action, eligible_qty=100, recognized=True)
