# filepath: tests/data_system/builders/test_registry.py
"""Formal feature and label builder registry tests."""

from __future__ import annotations

from src.data_system.builders.registry import FEATURE_BUILDERS, LABEL_BUILDERS


def test_registry_exposes_the_formal_feature_builder() -> None:
    assert tuple(FEATURE_BUILDERS) == (("tushare_daily_basic", "v1"),)


def test_registry_exposes_one_label_builder_per_maturity() -> None:
    assert {
        identity: builder.lookahead for identity, builder in LABEL_BUILDERS.items()
    } == {
        ("daily_close_return_rank_d1", "v1"): 1,
        ("daily_close_return_rank_d3", "v1"): 3,
        ("daily_close_return_rank_d5", "v1"): 5,
    }
    assert {builder.label_column for builder in LABEL_BUILDERS.values()} == {
        "y_rank_return"
    }
