# filepath: tests/trading/portfolio/test_engine.py
"""Executable-target tests for observable prices and signal availability."""

from __future__ import annotations

from collections.abc import Mapping
from itertools import permutations

import pytest

from src.trading.portfolio.engine import make_executable_targets, mark_to_market


def test_skipped_holdings_are_unchanged_and_occupy_target_capacity() -> None:
    targets = make_executable_targets(
        raw_targets={"000001": 100, "000002": 100},
        positions={"600000": 200},
        current_raw_prices={"600000": 10.0, "000001": 8.0, "000002": 7.0},
        hold_symbols={"600000"},
        max_positions=2,
    )

    assert targets == {"000001": 100, "000002": 0, "600000": 200}


def test_unscored_but_not_explicitly_skipped_holdings_remain_exit_targets() -> None:
    targets = make_executable_targets(
        raw_targets={},
        positions={"600000": 200},
        current_raw_prices={"600000": 10.0},
        hold_symbols=(),
    )

    assert targets == {"600000": 0}


def test_missing_score_preserves_a_pending_full_exit_as_an_explicit_zero_target() -> None:
    from decimal import Decimal
    from src.trading.core.events import OrderIntent, PendingOrder, Side
    order = OrderIntent(1, 'A', Side.SELL, 100, 1, 1, 10, Decimal(10))
    pending = PendingOrder(order, 100, Decimal(0), 100, Decimal(10))
    assert make_executable_targets(raw_targets={}, positions={'A': 100}, current_raw_prices={'A': 10},
        hold_symbols=('A',), pending_orders=(pending,)) == {'A': 0}


def test_valuation_uses_visible_prices_and_returns_owned_marks() -> None:
    positions = {"A": 100, "B": 200}
    current = {"A": 11.0}
    prior = {"A": 10.0, "B": 20.0, "C": 30.0}

    valuation = mark_to_market(positions=positions, current_raw_prices=current, last_mark_prices=prior)

    assert valuation.market_value == 5100.0
    assert valuation.mark_prices == {"A": 11.0, "B": 20.0, "C": 30.0}
    valuation.mark_prices["B"] = 99.0
    assert prior == {"A": 10.0, "B": 20.0, "C": 30.0}
    assert current == {"A": 11.0}
    assert positions == {"A": 100, "B": 200}


@pytest.mark.parametrize("prices", [{}, {"A": 0.0}, {"A": float("nan")}])
def test_valuation_does_not_replace_invalid_or_missing_marks_with_zero(prices: Mapping[str, float]) -> None:
    with pytest.raises(RuntimeError, match="held symbol=A"):
        mark_to_market(positions={"A": 100}, current_raw_prices=prices, last_mark_prices={})


def test_valuation_does_not_depend_on_position_iteration_order() -> None:
    prices = {"A": 10000000.0, "B": 0.03, "C": 0.03}
    values = {mark_to_market(positions=dict.fromkeys(order, 1), current_raw_prices=prices,
                            last_mark_prices={}).market_value for order in permutations(prices)}
    assert values == {10000000.06}
