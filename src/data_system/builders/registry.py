# filepath: src/data_system/builders/registry.py
"""Explicit feature and label builder registries."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from src.data_system.builders.feature_tushare_daily_basic import (
    TushareDailyBasicV1Builder,
)
from src.data_system.builders.label_daily_close_return_rank import (
    DailyCloseReturnRankV1Builder,
)

FEATURE_BUILDERS: Mapping[tuple[str, str], TushareDailyBasicV1Builder] = (
    MappingProxyType(
        {
            ("tushare_daily_basic", "v1"): TushareDailyBasicV1Builder(),
        }
    )
)

LABEL_BUILDERS: Mapping[tuple[str, str], DailyCloseReturnRankV1Builder] = (
    MappingProxyType(
        {
            ("daily_close_return_rank_d1", "v1"): DailyCloseReturnRankV1Builder(
                lookahead=1
            ),
            ("daily_close_return_rank_d3", "v1"): DailyCloseReturnRankV1Builder(
                lookahead=3
            ),
            ("daily_close_return_rank_d5", "v1"): DailyCloseReturnRankV1Builder(
                lookahead=5
            ),
        }
    )
)


def get_feature_builder(
    feature_set: str,
    version: str,
) -> TushareDailyBasicV1Builder:
    """Return the producer bound to one formal feature-set identity.

    Example:
        builder = get_feature_builder("tushare_daily_basic", "v1")
    """
    try:
        return FEATURE_BUILDERS[(feature_set, version)]
    except KeyError as exc:
        raise ValueError(
            f"unknown feature builder feature_set={feature_set!r} version={version!r}"
        ) from exc


def get_label_builder(
    label_set: str,
    version: str,
) -> DailyCloseReturnRankV1Builder:
    """Return the producer bound to one formal label-set identity.

    Example:
        builder = get_label_builder("daily_close_return_rank_d1", "v1")
    """
    try:
        return LABEL_BUILDERS[(label_set, version)]
    except KeyError as exc:
        raise ValueError(
            f"unknown label builder label_set={label_set!r} version={version!r}"
        ) from exc
