# filepath: tests/trading/signal/test_diagnostics.py
"""Operational row-count tests for model signal diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.trading.signal import diagnostics as diagnostics_module
from src.trading.signal.diagnostics import BasicSignalDiagnostics


def test_all_skipped_predictions_log_requested_scored_and_skipped_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_info = Mock()
    monkeypatch.setattr(
        diagnostics_module,
        "logs",
        SimpleNamespace(info=log_info),
    )

    BasicSignalDiagnostics(log_daily_only=False).on_after_predict(
        ts_us=1,
        scores={},
        requested_count=2,
        skipped_count=2,
    )

    log_info.assert_called_once_with("ts_us=1 requested=2 scored=0 skipped=2")
