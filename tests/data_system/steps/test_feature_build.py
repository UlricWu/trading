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
from src.data_system.context import DataContext
from src.data_system.steps import feature_build as feature_module
from src.data_system.steps.feature_build import FeatureBuildStep
from src.utils.path import PathManager


class _FeatureBuilder:
    def __init__(self) -> None:
        self.build_dates: list[str] = []

    def build(
        self,
        *,
        access: Access,
        trade_date: str,
    ) -> pa.Table:
        assert access is not None
        self.build_dates.append(trade_date)
        return pa.table({"feature": [1]})


def test_feature_step_binds_builder_and_materializes_each_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _FeatureBuilder()
    resolutions: list[tuple[str, str]] = []

    def get_builder(feature_set: str, version: str) -> _FeatureBuilder:
        resolutions.append((feature_set, version))
        return builder

    logger = Mock()
    monkeypatch.setattr(feature_module, "logs", logger)
    monkeypatch.setattr(feature_module, "get_feature_builder", get_builder)
    path_manager = PathManager(tmp_path)
    access = Mock(spec=Access)
    step = FeatureBuildStep(
        pm=path_manager,
        access=access,
        feature_sets={"daily": FeatureSetConfig(enabled=True, version="v1")},
    )

    context = DataContext(
        start="2026-07-20",
        end="2026-07-20",
        trade_dates=("2026-07-20",),
    )
    assert step.run(context) is context
    assert step.run(context) is context

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
    assert builder.build_dates == ["2026-07-20"]
    assert pq.read_table(output_path).to_pydict() == {"feature": [1]}
    assert [call.args[0] for call in logger.info.call_args_list] == [
        "✅ feature publish; feature_set=daily version=v1 "
        "trade_date=2026-07-20 rows=1",
        "♻️ feature meta hit; feature_set=daily version=v1 trade_date=2026-07-20",
    ]


def test_feature_step_rejects_an_unknown_identity_at_construction(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unknown feature builder"):
        FeatureBuildStep(
            pm=PathManager(tmp_path),
            access=Mock(spec=Access),
            feature_sets={"unknown": FeatureSetConfig(enabled=True, version="v1")},
        )
