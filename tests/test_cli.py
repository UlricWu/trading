# filepath: tests/test_cli.py
"""Public contract tests for the four CLI commands."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from src import cli
from src.config.backtest_config import BacktestMode
from src.data_system.pipeline import DataRunStatus
from src.utils.path import PathManager


@pytest.mark.parametrize(
    ("command", "workflow_name"),
    [
        ("data-standard", "run_offline_standard_data"),
        ("data-level2", "run_offline_level2_data"),
    ],
)
def test_data_command_calls_only_its_fixed_workflow(
    command: str,
    workflow_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(storage=SimpleNamespace(root=tmp_path))
    selected_workflow = Mock(return_value=DataRunStatus.SUCCESS)
    other_workflow = Mock(return_value=DataRunStatus.SUCCESS)
    other_name = (
        "run_offline_level2_data"
        if workflow_name == "run_offline_standard_data"
        else "run_offline_standard_data"
    )
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=config))
    monkeypatch.setattr(cli, workflow_name, selected_workflow)
    monkeypatch.setattr(cli, other_name, other_workflow)

    result = CliRunner().invoke(cli.app, [command, "2026-07-20"])

    assert result.exit_code == 0, result.output
    selected_workflow.assert_called_once()
    arguments = selected_workflow.call_args.kwargs
    assert arguments["trade_date"] == "2026-07-20"
    assert isinstance(arguments["path_manager"], PathManager)
    other_workflow.assert_not_called()


def test_data_skip_maps_to_exit_code_75(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(storage=SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=config))
    monkeypatch.setattr(
        cli,
        "run_offline_standard_data",
        Mock(return_value=DataRunStatus.SKIPPED),
    )

    result = CliRunner().invoke(cli.app, ["data-standard", "2026-07-20"])

    assert result.exit_code == 75


def test_train_uses_base_model_config_and_validated_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_config = object()
    config = SimpleNamespace(
        storage=SimpleNamespace(root=tmp_path),
        model=model_config,
    )
    load_config = Mock(return_value=config)
    run_training = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    monkeypatch.setattr(cli, "run_offline_training", run_training)

    result = CliRunner().invoke(
        cli.app,
        [
            "train",
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-20",
            "--experiment-id",
            "run-1",
        ],
    )

    assert result.exit_code == 0, result.output
    load_config.assert_called_once_with()
    arguments = run_training.call_args.kwargs
    assert arguments["model_config"] is model_config
    assert isinstance(arguments["path_manager"], PathManager)
    assert arguments["experiment_id"] == "run-1"
    assert arguments["start_date"] == "2026-07-01"
    assert arguments["end_date"] == "2026-07-20"


def test_backtest_applies_only_the_confirmed_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backtest_config = object()
    config = SimpleNamespace(
        storage=SimpleNamespace(root=tmp_path),
        backtest=backtest_config,
    )
    load_config = Mock(return_value=config)
    run_backtest = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    monkeypatch.setattr(cli, "run_daily_alpha_backtest", run_backtest)

    result = CliRunner().invoke(
        cli.app,
        [
            "backtest",
            "--mode",
            BacktestMode.FULL_BACKTEST.value,
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-20",
            "--experiment-id",
            "run-1",
            "--model-experiment",
            "training-1",
            "--strategy-json",
            '{"type":"threshold","params":{"threshold":0.5}}',
        ],
    )

    assert result.exit_code == 0, result.output
    load_config.assert_called_once_with(
        override={
            "backtest": {
                "backtest_mode": "full_backtest",
                "model": {"name": "training-1"},
                "strategy": {
                    "type": "threshold",
                    "params": {"threshold": 0.5, "target_quantity": 100},
                },
            }
        }
    )
    arguments = run_backtest.call_args.kwargs
    assert arguments["backtest_config"] is backtest_config
    assert isinstance(arguments["path_manager"], PathManager)
    assert arguments["experiment_id"] == "run-1"
    assert arguments["start_date"] == "2026-07-01"
    assert arguments["end_date"] == "2026-07-20"


@pytest.mark.parametrize(
    "arguments",
    [
        ["data-standard", "2026-02-30"],
        [
            "train",
            "--start",
            "2026-07-20",
            "--end",
            "2026-07-01",
            "--experiment-id",
            "run-1",
        ],
        [
            "train",
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-20",
            "--experiment-id",
            "-invalid",
        ],
        [
            "backtest",
            "--mode",
            "full_backtest",
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-20",
            "--experiment-id",
            "run-1",
            "--model-experiment",
            "../training-1",
            "--strategy-json",
            '{"type":"threshold","params":{"threshold":0.5}}',
        ],
        [
            "backtest",
            "--mode",
            "full_backtest",
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-20",
            "--experiment-id",
            "run-1",
            "--model-experiment",
            "training-1",
            "--strategy-json",
            '{"type":"threshold","params":{"qty":100}}',
        ],
    ],
)
def test_invalid_cli_input_fails_before_config_io(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_config = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)

    result = CliRunner().invoke(cli.app, arguments)

    assert result.exit_code == 2
    load_config.assert_not_called()


@pytest.mark.parametrize("legacy_command", ["data", "model"])
def test_legacy_commands_are_not_registered(legacy_command: str) -> None:
    result = CliRunner().invoke(cli.app, [legacy_command])

    assert result.exit_code == 2
