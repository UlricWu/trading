# filepath: tests/data_system/normalize/test_engine.py
"""Level-2 normalization route tests."""

from __future__ import annotations

import pytest

from src.data_system.normalize.engine import resolve_level2_event_spec


def test_level2_dataset_routes_carry_exchange_identity() -> None:
    sh_spec = resolve_level2_event_spec(
        raw_object="SH_Stock_OrderTrade",
        output="sh_trade",
    )
    sz_spec = resolve_level2_event_spec(
        raw_object="SZ_Trade",
        output="sz_trade",
    )

    assert sh_spec.exchange == "sh"
    assert sz_spec.exchange == "sz"


def test_level2_dataset_routes_reject_cross_exchange_identity() -> None:
    with pytest.raises(ValueError, match="unsupported Level-2 raw/output"):
        resolve_level2_event_spec(
            raw_object="SH_Stock_OrderTrade",
            output="sz_trade",
        )
