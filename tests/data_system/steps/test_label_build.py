# filepath: tests/data_system/steps/test_label_build.py
"""Behavior tests for label materialization."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.data_system.context import DataContext
from src.data_system.steps import label_build as label_module
from src.data_system.steps.label_build import LabelBuildStep
from src.utils.path import PathManager


class _LabelBuilder:
    label_column = "y_rank_return"

    def __init__(self, lookahead: int) -> None:
        self.lookahead = lookahead
        self.build_dates: tuple[str, ...] | None = None

    def build(
        self,
        *,
        access: Access,
        trade_dates: Sequence[str],
    ) -> pa.Table:
        assert access is not None
        self.build_dates = tuple(trade_dates)
        return pa.table({"label": [float(self.lookahead)]})


def test_label_step_runs_each_single_maturity_set_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builders = {
        "daily_close_return_rank_d1": _LabelBuilder(1),
        "daily_close_return_rank_d3": _LabelBuilder(3),
        "daily_close_return_rank_d5": _LabelBuilder(5),
    }
    resolutions: list[tuple[str, str]] = []

    def get_builder(label_set: str, version: str) -> _LabelBuilder:
        resolutions.append((label_set, version))
        return builders[label_set]

    logger = Mock()
    monkeypatch.setattr(label_module, "logs", logger)
    monkeypatch.setattr(label_module, "get_label_builder", get_builder)
    path_manager = PathManager(tmp_path)
    windows = {
        2: ["2026-07-17", "2026-07-20"],
        4: ["2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20"],
        6: [
            "2026-07-13",
            "2026-07-14",
            "2026-07-15",
            "2026-07-16",
            "2026-07-17",
            "2026-07-20",
        ],
    }
    access = Mock(spec=Access)
    access.recent_trade_dates.side_effect = lambda *, end_date, sessions: windows[
        sessions
    ]
    step = LabelBuildStep(
        pm=path_manager,
        access=access,
        label_versions={label_set: "v1" for label_set in builders},
    )

    context = DataContext(
        start="2026-07-20",
        end="2026-07-20",
        trade_dates=("2026-07-20",),
    )
    assert step.run(context) is context
    assert step.run(context) is context

    expected_targets = {
        "daily_close_return_rank_d1": "2026-07-17",
        "daily_close_return_rank_d3": "2026-07-15",
        "daily_close_return_rank_d5": "2026-07-13",
    }
    for label_set, target_date in expected_targets.items():
        output_path = path_manager.label_data(
            label_set=label_set,
            version="v1",
            trade_date=target_date,
        )
        meta.require(
            pm=path_manager,
            meta_path=path_manager.label_meta(
                label_set=label_set,
                version="v1",
                trade_date=target_date,
            ),
            expected_payload_path=output_path,
        )
        assert pq.read_table(output_path).to_pydict() == {
            "label": [float(builders[label_set].lookahead)]
        }
    assert resolutions == [(label_set, "v1") for label_set in builders]
    assert access.recent_trade_dates.call_count == 6
    for label_set, builder in builders.items():
        assert builder.build_dates == tuple(windows[builder.lookahead + 1])
    messages = [call.args[0] for call in logger.info.call_args_list]
    assert [message.split(";", 1)[0] for message in messages] == [
        "✅ label publish",
        "✅ label publish",
        "✅ label publish",
        "♻️ label meta hit",
        "♻️ label meta hit",
        "♻️ label meta hit",
    ]


def test_label_step_rejects_an_unknown_identity_at_construction(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unknown label builder"):
        LabelBuildStep(
            pm=PathManager(tmp_path),
            access=Mock(spec=Access),
            label_versions={"unknown": "v1"},
        )
