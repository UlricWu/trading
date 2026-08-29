# filepath: tests/test_cli.py
"""Public contract tests for the seven CLI commands."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from src import cli
from src.config.backtest_config import BacktestMode
from src.utils.path import PathManager


def test_data_calendar_bootstraps_through_the_current_market_year(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(storage_root=tmp_path)
    workflow = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=config))
    monkeypatch.setattr(cli.DateTimeUtils, "today", Mock(return_value="2026-08-21"))
    monkeypatch.setattr(cli, "run_trade_calendar_bootstrap", workflow)

    result = CliRunner().invoke(cli.app, ["data-calendar"])

    assert result.exit_code == 0, result.output
    arguments = workflow.call_args.kwargs
    assert arguments["app_config"] is config
    assert arguments["as_of_date"] == "2026-08-21"
    assert isinstance(arguments["path_manager"], PathManager)


def test_standard_fact_bootstrap_passes_one_validated_explicit_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(storage_root=tmp_path)
    workflow = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=config))
    monkeypatch.setattr(cli, "run_standard_fact_bootstrap", workflow)

    result = CliRunner().invoke(
        cli.app,
        [
            "data-standard-bootstrap",
            "--start",
            "2019-01-01",
            "--end",
            "2019-04-03",
        ],
    )

    assert result.exit_code == 0, result.output
    arguments = workflow.call_args.kwargs
    assert arguments["app_config"] is config
    assert arguments["submission"].start == "2019-01-01"
    assert arguments["submission"].end == "2019-04-03"
    assert isinstance(arguments["path_manager"], PathManager)


@pytest.mark.parametrize(
    ("command", "expected_kind"),
    [
        ("data-standard", "data-standard"),
        ("data-level2", "data-level2"),
    ],
)
def test_data_command_passes_one_validated_range_submission(
    command: str,
    expected_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(storage_root=tmp_path)
    workflow = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=config))
    monkeypatch.setattr(cli, "run_offline_data", workflow)

    result = CliRunner().invoke(
        cli.app,
        [
            command,
            "--start",
            "2026-07-01",
            "--end",
            "2026-07-20",
        ],
    )

    assert result.exit_code == 0, result.output
    submission = workflow.call_args.kwargs["submission"]
    assert submission.kind == expected_kind
    assert submission.start == "2026-07-01"
    assert submission.end == "2026-07-20"
    assert isinstance(workflow.call_args.kwargs["path_manager"], PathManager)


def test_feature_backfill_passes_one_exact_identity_and_target_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(storage_root=tmp_path)
    workflow = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=config))
    monkeypatch.setattr(cli, "run_feature_backfill", workflow)

    result = CliRunner().invoke(
        cli.app,
        [
            "data-feature-backfill",
            "--feature-set",
            "tushare_daily_basic",
            "--version",
            "v1",
            "--start",
            "2019-04-04",
            "--end",
            "2019-07-05",
        ],
    )

    assert result.exit_code == 0, result.output
    arguments = workflow.call_args.kwargs
    submission = arguments["submission"]
    assert submission.feature_set == "tushare_daily_basic"
    assert submission.version == "v1"
    assert submission.start == "2019-04-04"
    assert submission.end == "2019-07-05"
    assert isinstance(arguments["path_manager"], PathManager)


def test_train_passes_base_model_config_and_complete_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_config = object()
    load_config = Mock(
        return_value=SimpleNamespace(
            storage_root=tmp_path,
            model=model_config,
        )
    )
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
    assert arguments["submission"].start == "2026-07-01"
    assert arguments["submission"].end == "2026-07-20"
    assert arguments["experiment_id"] == "run-1"
    assert isinstance(arguments["path_manager"], PathManager)


def test_backtest_loads_static_config_without_runtime_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backtest_config = object()
    load_config = Mock(
        return_value=SimpleNamespace(
            storage_root=tmp_path,
            backtest=backtest_config,
        )
    )
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
    load_config.assert_called_once_with()
    arguments = run_backtest.call_args.kwargs
    submission = arguments["submission"]
    assert arguments["backtest_config"] is backtest_config
    assert submission.mode is BacktestMode.FULL_BACKTEST
    assert submission.model_experiment == "training-1"
    assert submission.strategy.params.target_quantity == 100
    assert arguments["experiment_id"] == "run-1"
    assert isinstance(arguments["path_manager"], PathManager)


@pytest.mark.parametrize(
    ("arguments", "workflow_name"),
    [
        (
            [
                "train",
                "--start",
                "2026-07-01",
                "--end",
                "2026-07-02",
                "--experiment-id",
                "run-1",
            ],
            "run_offline_training",
        ),
        (
            [
                "backtest",
                "--mode",
                "full_backtest",
                "--start",
                "2026-07-01",
                "--end",
                "2026-07-02",
                "--experiment-id",
                "run-1",
                "--model-experiment",
                "training-1",
                "--strategy-json",
                '{"type":"threshold","params":{"threshold":0.5}}',
            ],
            "run_daily_alpha_backtest",
        ),
    ],
)
def test_empty_runtime_schedule_propagates_as_exit_code_1(
    arguments: list[str],
    workflow_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli.AppConfig,
        "load",
        Mock(
            return_value=SimpleNamespace(
                storage_root=tmp_path,
                model=object(),
                backtest=object(),
            )
        ),
    )
    monkeypatch.setattr(
        cli,
        workflow_name,
        Mock(side_effect=ValueError("empty schedule")),
    )

    result = CliRunner().invoke(cli.app, arguments)

    assert result.exit_code == 1


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "data-calendar",
            "--start",
            "2026-01-01",
        ],
        [
            "data-standard",
            "--start",
            "2026-02-30",
            "--end",
            "2026-03-01",
        ],
        [
            "data-standard-bootstrap",
            "--start",
            "2019-04-02",
            "--end",
            "2019-01-01",
        ],
        [
            "data-feature-backfill",
            "--feature-set",
            "../daily",
            "--version",
            "v1",
            "--start",
            "2019-04-03",
            "--end",
            "2019-07-01",
        ],
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
