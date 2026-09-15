# filepath: tests/trading/engines/test_replay.py
"""Executable-target tests for replay-specific availability."""

from __future__ import annotations

from src.trading.engines.replay import make_executable_targets


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
