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
from src.config.data_config import LabelSetConfig
from src.data_system.context import DataContext
from src.data_system.steps import label_build as label_module
from src.data_system.steps.label_build import LabelBuildStep
from src.utils.path import PathManager


class _LabelBuilder:
    key_columns = ("symbol", "trade_date")
    output_columns = ("short", "long")

    def __init__(self) -> None:
        self.read_dates: tuple[str, ...] | None = None

    def target_lookahead(self, label_column: str) -> int:
        return {"short": 1, "long": 2}[label_column]

    def read_input(
        self,
        *,
        access: Access,
        pm: PathManager,
        processed_version: str,
        trade_dates: Sequence[str],
    ) -> pa.Table:
        assert access is not None
        assert processed_version == "v1"
        self.read_dates = tuple(trade_dates)
        return pa.table({"value": [1]})

    def build_partition(
        self,
        table: pa.Table,
    ) -> pa.Table:
        return pa.table({"label": [1.0]})


def test_label_step_owns_lookahead_and_uses_the_first_date_as_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _LabelBuilder()
    resolutions: list[tuple[str, str]] = []

    def get_builder(label_set: str, version: str) -> _LabelBuilder:
        resolutions.append((label_set, version))
        return builder

    monkeypatch.setattr(label_module, "get_label_builder", get_builder)
    path_manager = PathManager(tmp_path)
    input_dates = ("2026-07-16", "2026-07-17", "2026-07-20")
    access = Mock(spec=Access)
    access.recent_trade_dates.return_value = list(input_dates)
    step = LabelBuildStep(
        pm=path_manager,
        access=access,
        processed_version="v1",
        label_sets={"forward_rank": LabelSetConfig(enabled=True, version="v1")},
    )

    step.run(
        DataContext(
            start="2026-07-20",
            end="2026-07-20",
            trade_dates=("2026-07-20",),
        )
    )

    output_path = path_manager.label_data(
        label_set="forward_rank",
        version="v1",
        trade_date="2026-07-16",
    )
    meta.require(
        pm=path_manager,
        meta_path=path_manager.label_meta(
            label_set="forward_rank",
            version="v1",
            trade_date="2026-07-16",
        ),
        expected_payload_path=output_path,
    )
    assert resolutions == [("forward_rank", "v1")]
    access.recent_trade_dates.assert_called_once_with(
        end_date="2026-07-20",
        sessions=3,
    )
    assert builder.read_dates == input_dates
    assert pq.read_table(output_path).to_pydict() == {"label": [1.0]}


def test_label_step_rejects_an_unknown_identity_at_construction(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unknown label builder"):
        LabelBuildStep(
            pm=PathManager(tmp_path),
            access=Mock(spec=Access),
            processed_version="v1",
            label_sets={"unknown": LabelSetConfig(enabled=True, version="v1")},
        )
