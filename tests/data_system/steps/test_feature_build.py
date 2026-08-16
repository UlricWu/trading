# filepath: tests/data_system/steps/test_feature_build.py
"""Behavior tests for feature materialization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.config.data_config import FeatureSetConfig
from src.data_system.steps import feature_build as feature_module
from src.data_system.steps.feature_build import FeatureBuildStep
from src.utils.path import PathManager


class _FeatureBuilder:
    key_columns = ("symbol", "trade_date")
    output_columns = ("feature",)

    def __init__(self) -> None:
        self.read_dates: list[str] = []

    def read_input(
        self,
        *,
        access: Access,
        pm: PathManager,
        processed_version: str,
        trade_date: str,
    ) -> pa.Table:
        assert access is not None
        assert processed_version == "v1"
        self.read_dates.append(trade_date)
        return pa.table({"value": [1]})

    def build_partition(self, table: pa.Table) -> pa.Table:
        return pa.table({"feature": table["value"]})


def test_feature_step_binds_builder_and_materializes_each_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _FeatureBuilder()
    resolutions: list[tuple[str, str]] = []

    def get_builder(feature_set: str, version: str) -> _FeatureBuilder:
        resolutions.append((feature_set, version))
        return builder

    monkeypatch.setattr(feature_module, "get_feature_builder", get_builder)
    path_manager = PathManager(tmp_path)
    access = Mock(spec=Access)
    step = FeatureBuildStep(
        pm=path_manager,
        access=access,
        processed_version="v1",
        feature_sets={"daily": FeatureSetConfig(enabled=True, version="v1")},
    )

    step("2026-07-20")

    output_path = path_manager.feature_data(
        feature_set="daily",
        version="v1",
        trade_date="2026-07-20",
    )
    meta.require(
        pm=path_manager,
        meta_path=path_manager.feature_meta(
            feature_set="daily",
            version="v1",
            trade_date="2026-07-20",
        ),
        expected_payload_path=output_path,
    )
    assert resolutions == [("daily", "v1")]
    assert builder.read_dates == ["2026-07-20"]
    assert pq.read_table(output_path).to_pydict() == {"feature": [1]}


def test_feature_step_rejects_an_unknown_identity_at_construction(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unknown feature builder"):
        FeatureBuildStep(
            pm=PathManager(tmp_path),
            access=Mock(spec=Access),
            processed_version="v1",
            feature_sets={"unknown": FeatureSetConfig(enabled=True, version="v1")},
        )
