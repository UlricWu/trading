# filepath: tests/data_system/steps/test_feature_build.py
"""Behavior tests for feature materialization."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.data_system.context import DataContext
from src.data_system.steps import feature_build as feature_module
from src.data_system.steps.feature_build import FeatureBuildStep
from src.utils.path import PathManager


class _FeatureBuilder:
    lookback_sessions = 2

    def __init__(self) -> None:
        self.build_windows: list[tuple[str, ...]] = []

    def build(
        self,
        *,
        access: Access,
        trade_dates: Sequence[str],
    ) -> pa.Table:
        assert access is not None
        self.build_windows.append(tuple(trade_dates))
        return pa.table({"feature": [1]})


class _FailOnceFeatureBuilder:
    lookback_sessions = 1

    def __init__(self, *, failing_target: str) -> None:
        self._failing_target = failing_target
        self._has_failed = False
        self.build_windows: list[tuple[str, ...]] = []

    def build(
        self,
        *,
        access: Access,
        trade_dates: Sequence[str],
    ) -> pa.Table:
        assert access is not None
        window = tuple(trade_dates)
        self.build_windows.append(window)
        if window[-1] == self._failing_target and not self._has_failed:
            self._has_failed = True
            raise RuntimeError("candidate failure")
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
    access.recent_trade_dates.return_value = [
        "2026-07-16",
        "2026-07-17",
        "2026-07-20",
    ]
    step = FeatureBuildStep(
        pm=path_manager,
        access=access,
        feature_versions={"daily": "v1"},
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
    assert builder.build_windows == [
        ("2026-07-16", "2026-07-17", "2026-07-20")
    ]
    access.recent_trade_dates.assert_called_once_with(
        end_date="2026-07-20",
        sessions=3,
    )
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
            feature_versions={"unknown": "v1"},
        )


def test_feature_step_keeps_completed_partitions_and_resumes_from_meta_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _FailOnceFeatureBuilder(failing_target="2026-07-20")
    monkeypatch.setattr(
        feature_module,
        "get_feature_builder",
        Mock(return_value=builder),
    )
    path_manager = PathManager(tmp_path)
    access = Mock(spec=Access)
    windows = {
        "2026-07-17": ["2026-07-16", "2026-07-17"],
        "2026-07-20": ["2026-07-17", "2026-07-20"],
    }
    access.recent_trade_dates.side_effect = (
        lambda *, end_date, sessions: windows[end_date] if sessions == 2 else []
    )
    step = FeatureBuildStep(
        pm=path_manager,
        access=access,
        feature_versions={"daily": "v1"},
    )
    context = DataContext(
        start="2026-07-17",
        end="2026-07-20",
        trade_dates=("2026-07-17", "2026-07-20"),
    )

    with pytest.raises(RuntimeError, match="candidate failure"):
        step.run(context)

    first_payload = path_manager.feature_data(
        feature_set="daily",
        version="v1",
        trade_date="2026-07-17",
    )
    meta.require(
        pm=path_manager,
        meta_path=path_manager.feature_meta(
            feature_set="daily",
            version="v1",
            trade_date="2026-07-17",
        ),
        expected_payload_path=first_payload,
    )
    assert not path_manager.feature_meta(
        feature_set="daily",
        version="v1",
        trade_date="2026-07-20",
    ).exists()

    assert step.run(context) is context

    second_payload = path_manager.feature_data(
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
        expected_payload_path=second_payload,
    )
    assert builder.build_windows == [
        ("2026-07-16", "2026-07-17"),
        ("2026-07-17", "2026-07-20"),
        ("2026-07-17", "2026-07-20"),
    ]
    assert [
        call.kwargs["end_date"] for call in access.recent_trade_dates.call_args_list
    ] == ["2026-07-17", "2026-07-20", "2026-07-20"]
