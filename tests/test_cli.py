# filepath: tests/test_cli.py
"""Public contract tests for the CLI commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from src import cli
from src.jobs.requests import build_cli_command, create_training_submission
from src.utils.path import PathManager


def _training_args() -> list[str]:
    return [
        "--train-start", "2026-07-01", "--train-end", "2026-07-20",
        "--model", "sgd_regression", "--feature-group", "daily_basic_selected_v1",
        "--label-group", "daily_close_return_rank_d1/v1",
    ]


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


def test_fusion_backfill_passes_one_validated_target_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    load_config = Mock(return_value=SimpleNamespace(storage_root=tmp_path))
    workflow = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    monkeypatch.setattr(cli, "run_stock_1430_fusion_backfill", workflow)
    result = CliRunner().invoke(
        cli.app,
        [
            "data-stock-1430-fusion-backfill",
            "--start",
            "2026-05-06",
            "--end",
            "2026-05-07",
        ],
    )
    assert result.exit_code == 0, result.output
    load_config.assert_called_once_with()
    workflow.assert_called_once()
    arguments = workflow.call_args.kwargs
    assert set(arguments) == {"path_manager", "submission"}
    assert isinstance(arguments["path_manager"], PathManager)
    assert arguments["submission"].start == "2026-05-06"
    assert arguments["submission"].end == "2026-05-07"


def test_fusion_backfill_propagates_workflow_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli.AppConfig, "load", Mock(return_value=SimpleNamespace(storage_root=tmp_path))
    )
    failure = FileNotFoundError("required daily P is unavailable")
    monkeypatch.setattr(
        cli, "run_stock_1430_fusion_backfill", Mock(side_effect=failure)
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "data-stock-1430-fusion-backfill",
            "--start",
            "2026-05-06",
            "--end",
            "2026-05-06",
        ],
    )
    assert result.exit_code == 1
    assert result.exception is failure


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


def test_level2_minute_backfill_passes_one_validated_target_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(storage_root=tmp_path)
    workflow = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=config))
    monkeypatch.setattr(cli, "run_level2_minute_backfill", workflow)

    result = CliRunner().invoke(
        cli.app,
        [
            "data-level2-minute-backfill",
            "--start",
            "2025-11-18",
            "--end",
            "2025-11-19",
        ],
    )

    assert result.exit_code == 0, result.output
    arguments = workflow.call_args.kwargs
    assert arguments["submission"].start == "2025-11-18"
    assert arguments["submission"].end == "2025-11-19"
    assert isinstance(arguments["path_manager"], PathManager)


def test_stock_1430_backfill_passes_one_validated_target_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(storage_root=tmp_path)
    workflow = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=config))
    monkeypatch.setattr(cli, "run_stock_1430_backfill", workflow)

    result = CliRunner().invoke(
        cli.app,
        [
            "data-stock-1430-backfill",
            "--start",
            "2026-05-06",
            "--end",
            "2026-05-07",
        ],
    )

    assert result.exit_code == 0, result.output
    arguments = workflow.call_args.kwargs
    assert arguments["submission"].start == "2026-05-06"
    assert arguments["submission"].end == "2026-05-07"
    assert isinstance(arguments["path_manager"], PathManager)


@pytest.mark.parametrize("eval_end", [None, "2026-08-10", "2026-08-31"])
def test_train_command_round_trip_preserves_named_choices_and_ranges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    eval_end: str | None,
) -> None:
    models = {"sgd_regression": object()}
    feature_groups = {"daily_basic_selected_v1": object()}
    load_config = Mock(
        return_value=SimpleNamespace(
            storage_root=tmp_path,
            models=models, feature_groups=feature_groups,
        )
    )
    run_training = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    monkeypatch.setattr(cli, "run_offline_training", run_training)

    submission = create_training_submission(
        train_range={"start": "2026-07-01", "end": "2026-07-20"},
        eval_range={"start": "2026-08-10", "end": eval_end} if eval_end else None,
        model="sgd_regression", dataset={
            "feature_group": "daily_basic_selected_v1",
            "label_group": "daily_close_return_rank_d1/v1",
        },
    )
    command = build_cli_command(submission, "run-1")
    result = CliRunner().invoke(cli.app, command[3:])

    assert result.exit_code == 0, result.output
    load_config.assert_called_once_with()
    arguments = run_training.call_args.kwargs
    assert arguments["models"] is models
    assert arguments["feature_groups"] is feature_groups
    assert arguments["submission"] == submission
    assert arguments["experiment_id"] == "run-1"
    assert isinstance(arguments["path_manager"], PathManager)


@pytest.mark.parametrize(("mode", "workflow"), [
    ("signal_eval", "run_signal_evaluation"), ("risk_rule_eval", "run_risk_rule_evaluation"),
    ("execution_eval", "run_execution_evaluation"), ("strategy_eval", "run_strategy_evaluation"),
    ("full_backtest", "run_full_backtest"),
])
def test_backtest_routes_validated_input_to_its_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    backtest_payload: Callable[[str], dict[str, object]], mode: str, workflow: str,
) -> None:
    config = object()
    load_config = Mock(return_value=SimpleNamespace(storage_root=tmp_path, backtest=config))
    run = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    monkeypatch.setattr(cli, workflow, run)
    fields = backtest_payload(mode)
    del fields["kind"], fields["mode"]
    result = CliRunner().invoke(cli.app, ["backtest", "--mode", mode,
        "--experiment-id", "run-1", "--input-json", json.dumps(fields)])
    assert result.exit_code == 0, result.output
    load_config.assert_called_once_with()
    args = run.call_args.kwargs
    assert args["submission"].mode.value == mode
    assert args["experiment_id"] == "run-1"
    assert isinstance(args["path_manager"], PathManager)
    if mode in ("signal_eval", "strategy_eval", "full_backtest"):
        assert args["backtest_config"] is config
    else:
        assert "backtest_config" not in args


@pytest.mark.parametrize(("old_mode", "mode"), [
    ("tradable_alpha_eval", "strategy_eval"), ("risk_eval", "risk_rule_eval"),
])
def test_backtest_rejects_retired_modes_before_loading_config(
    monkeypatch: pytest.MonkeyPatch, backtest_payload: Callable[[str], dict[str, object]],
    old_mode: str, mode: str,
) -> None:
    load_config = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    fields = backtest_payload(mode)
    del fields["kind"], fields["mode"]
    result = CliRunner().invoke(cli.app, ["backtest", "--mode", old_mode,
        "--experiment-id", "run-1", "--input-json", json.dumps(fields)])
    assert result.exit_code == 2
    assert "mode" in result.output
    load_config.assert_not_called()


@pytest.mark.parametrize(
    ("arguments", "workflow_name"),
    [
        (
            [
                "train", *_training_args(),
                "--experiment-id",
                "run-1",
            ],
            "run_offline_training",
        ),
        (
            [
                "backtest", "--mode", "signal_eval", "--experiment-id", "run-1",
                "--input-json", '{"start":"2026-07-01","end":"2026-07-02","signal":{"type":"recorded","prediction_refs":[]}}',
            ],
            "run_signal_evaluation",
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
                models=object(), feature_groups=object(),
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
            "data-level2-minute-backfill",
            "--start",
            "2025-11-19",
            "--end",
            "2025-11-18",
        ],
        [
            "data-stock-1430-backfill",
            "--start",
            "2026-05-07",
            "--end",
            "2026-05-06",
        ],
        [
            "data-stock-1430-fusion-backfill",
            "--start",
            "2026-05-07",
            "--end",
            "2026-05-06",
        ],
        [
            "data-stock-1430-fusion-backfill",
            "--start",
            "2026-05-06",
            "--end",
            "2026-05-06",
            "--version",
            "v2",
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
            "train", *_training_args(),
            "--experiment-id", "-invalid",
        ],
        [
            "train", *_training_args(), "--experiment-id", "run-1",
            "--eval-start", "2026-08-10",
        ],
        [
            "train", *_training_args(), "--experiment-id", "run-1",
            "--eval-end", "2026-08-31",
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
            "--model-ref",
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
