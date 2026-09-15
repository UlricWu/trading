# filepath: tests/data_system/normalize/test_level2_security.py
"""Behavior tests for Level-2 exchange security classification."""

from __future__ import annotations

from typing import Literal

import pyarrow as pa
import pytest

from src.data_system.normalize.level2_security import (
    resolve_level2_security_type,
)


@pytest.mark.parametrize(
    ("exchange", "symbols", "expected"),
    [
        (
            "sh",
            ["600000", "510300", "113001"],
            ["stock", "etf", "convertible_bond"],
        ),
        (
            "sz",
            ["000001", "159001", "131810"],
            ["stock", "etf", "bond_repo"],
        ),
    ],
)
def test_resolve_level2_security_type_classifies_exchange_ranges(
    exchange: Literal["sh", "sz"],
    symbols: list[str],
    expected: list[str],
) -> None:
    table = pa.table({"symbol": symbols})

    resolved = resolve_level2_security_type(table, exchange=exchange)

    assert resolved["security_type"].to_pylist() == expected


def test_resolve_level2_security_type_rejects_unsupported_segment() -> None:
    with pytest.raises(ValueError, match="unsupported security_type segment"):
        resolve_level2_security_type(
            pa.table({"symbol": ["133000"]}),
            exchange="sh",
        )


def test_resolve_level2_security_type_rejects_invalid_symbol() -> None:
    with pytest.raises(ValueError, match="invalid SecurityID"):
        resolve_level2_security_type(
            pa.table({"symbol": ["not-a-symbol"]}),
            exchange="sz",
        )
