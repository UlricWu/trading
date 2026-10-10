# filepath: tests/trading/portfolio/test_engine.py
"""Executable-target tests for observable prices and signal availability."""

from __future__ import annotations

from src.trading.portfolio.engine import make_executable_targets


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
