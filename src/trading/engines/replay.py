# filepath: src/trading/engines/replay.py
from __future__ import annotations

import math
from collections.abc import Collection, Mapping


def make_executable_targets(
    *,
    raw_targets: Mapping[str, int],
    positions: Mapping[str, int],
    current_raw_prices: Mapping[str, float],
    hold_symbols: Collection[str],
    max_positions: int | None = None,
) -> dict[str, int]:
    """Keep unavailable holdings and adapt the remaining executable targets.

    Example:
        targets = make_executable_targets(
            raw_targets={},
            positions={"600000": 100},
            current_raw_prices={"600000": 10.0},
            hold_symbols={"600000"},
            max_positions=1,
        )
    """
    executable = {
        str(symbol): _require_nonnegative_quantity(qty, label="raw target")
        for symbol, qty in raw_targets.items()
    }
    normalized_positions = {
        str(symbol): _require_nonnegative_quantity(qty, label="position")
        for symbol, qty in positions.items()
    }
    if max_positions is not None and max_positions < 0:
        raise ValueError("max_positions must be non-negative or None")
    priced_symbols = {str(symbol) for symbol in current_raw_prices}
    held_without_score = {str(symbol) for symbol in hold_symbols}
    locked_symbols: set[str] = set()

    for symbol, qty in normalized_positions.items():
        sym = str(symbol)
        current_qty = int(qty)
        if current_qty <= 0:
            continue
        if sym in held_without_score or sym not in priced_symbols:
            executable[sym] = current_qty
            locked_symbols.add(sym)
            continue
        if sym not in executable:
            executable[sym] = 0

    if max_positions is not None:
        remaining_slots = int(max_positions) - sum(
            1
            for symbol in locked_symbols
            if int(executable.get(symbol, 0)) > 0
        )
        kept = 0
        for symbol, qty in executable.items():
            if symbol in locked_symbols or int(qty) <= 0:
                continue
            if kept < remaining_slots:
                kept += 1
                continue
            executable[symbol] = 0

    return executable


def mark_to_market(
    *,
    positions: Mapping[str, int],
    current_raw_prices: Mapping[str, float],
    last_mark_prices: Mapping[str, float],
) -> tuple[float, dict[str, float]]:
    """Value current positions with current raw close or prior visible marks."""
    current_prices = {
        str(symbol): price
        for symbol, price in current_raw_prices.items()
    }
    prior_marks = {
        str(symbol): price
        for symbol, price in last_mark_prices.items()
    }
    updated_marks = dict(prior_marks)
    market_value = 0.0

    for symbol, qty in positions.items():
        sym = str(symbol)
        normalized_qty = _require_nonnegative_quantity(qty, label="position")
        price = current_prices.get(sym)
        if price is None:
            price = prior_marks.get(sym)
        if price is None:
            raise RuntimeError(
                "[replay] missing raw close and last mark for held "
                f"symbol={symbol}"
            )
        if not math.isfinite(float(price)) or float(price) <= 0.0:
            raise RuntimeError(
                "[replay] invalid raw close mark for held "
                f"symbol={symbol}: price={price}"
            )
        updated_marks[sym] = float(price)
        market_value += normalized_qty * float(price)

    return float(market_value), updated_marks


def _require_nonnegative_quantity(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer: {value!r}")
    return value
