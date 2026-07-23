# filepath: src/data_system/builders/registry.py
"""Explicit feature and label builder registries."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from src.data_system.builders.base import FeatureBuilder, LabelBuilder
from src.data_system.builders.feature_tushare_daily_basic import (
    TushareDailyBasicV1Builder,
)
from src.data_system.builders.label_daily_forward_excess_rank import (
    DailyForwardExcessRankV1Builder,
)
from src.data_system.builders.label_daily_t1_net_excess_rank import (
    DailyT1NetExcessRankV1Builder,
)


FEATURE_BUILDERS: Mapping[tuple[str, str], FeatureBuilder] = MappingProxyType({
    ("tushare_daily_basic", "v1"): TushareDailyBasicV1Builder(),
})

LABEL_BUILDERS: Mapping[tuple[str, str], LabelBuilder] = MappingProxyType({
    ("daily_t1_net_excess_rank", "v1"): DailyT1NetExcessRankV1Builder(),
    ("daily_forward_excess_rank", "v1"): DailyForwardExcessRankV1Builder(),
})


def get_feature_builder(feature_set: str, version: str) -> FeatureBuilder:
    try:
        return FEATURE_BUILDERS[(feature_set, version)]
    except KeyError as exc:
        raise ValueError(
            f"unknown feature builder feature_set={feature_set!r} version={version!r}"
        ) from exc


def get_label_builder(label_set: str, version: str) -> LabelBuilder:
    try:
        return LABEL_BUILDERS[(label_set, version)]
    except KeyError as exc:
        raise ValueError(
            f"unknown label builder label_set={label_set!r} version={version!r}"
        ) from exc
