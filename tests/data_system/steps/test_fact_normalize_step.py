# filepath: tests/data_system/steps/test_fact_normalize_step.py
"""Path selection tests for fact normalization."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pyarrow as pa
import pytest

from src.access import meta
from src.config.app_config import AppConfig
from src.data_system.context import DataContext
from src.data_system.normalize.profiles import NormalizeOutput
from src.data_system.steps import fact_normalize_step
from src.data_system.steps.fact_normalize_step import FactNormalizeStep
from src.utils.path import PathManager


@pytest.mark.parametrize(
    ("staging_bytes", "expected_source"),
    [(b"same-size", "staging"), (b"different-size", "raw"), (None, "raw")],
)
def test_normalize_uses_staging_only_when_size_matches_raw(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    staging_bytes: bytes | None,
    expected_source: str,
) -> None:
    pm = PathManager(tmp_path)
    trade_date = "2026-05-01"
    raw_path = pm.raw_payload(
        broker="broker",
        source_name="source",
        trade_date=trade_date,
        payload_file="source.csv.7z",
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"same-size")
    meta.write(payload_path=raw_path, storage_root=pm.storage_root)

    staging_path = pm.staging_payload(
        broker="broker",
        source_name="source",
        trade_date=trade_date,
        payload_file=raw_path.name,
    )
    if staging_bytes is not None:
        staging_path.parent.mkdir(parents=True)
        staging_path.write_bytes(staging_bytes)

    selected_inputs: list[Path] = []

    def normalize_profile(
        *,
        input_file: Path,
        output_name: Path,
        raw_object: str,
        target_name: str,
        trade_date: str,
    ) -> NormalizeOutput:
        selected_inputs.append(input_file)
        return NormalizeOutput(table=pa.table({"value": [1]}))

    monkeypatch.setattr(
        fact_normalize_step,
        "NORMALIZE_PROFILES",
        {("broker", "v1"): normalize_profile},
    )
    app_config = cast(
        AppConfig,
        SimpleNamespace(
            data=SimpleNamespace(
                brokers={"broker": SimpleNamespace(normalize_profile="v1")},
                sources={
                    "source": SimpleNamespace(
                        broker="broker",
                        enabled=True,
                        outputs=["output"],
                        raw_object="raw_object",
                    )
                },
            )
        ),
    )

    FactNormalizeStep(app_cfg=app_config, inst=None).run(
        DataContext(trade_date=trade_date, pm=pm)
    )

    expected_path = staging_path if expected_source == "staging" else raw_path
    assert selected_inputs == [expected_path]
