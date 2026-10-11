# filepath: tests/test_cli.py
"""Public contract tests for the CLI commands."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from src import cli, logs
from src.jobs.requests import build_cli_command, create_training_submission
from src.utils.path import PathManager


@pytest.fixture
def backtest_log(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "backtest.log"
    sink = logs.add(path, format="{message}", level="INFO")
    try:
        yield path
    finally:
        logs.remove(sink)


def _logged_parameters(log_path: Path, *, experiment_name: str) -> dict[str, object]:
    lines = log_path.read_text().splitlines()
    assert lines[0] == f"▶️ 回测任务; experiment={experiment_name}"
    assert sum(line.startswith("▶️ 回测任务;") for line in lines) == 1
    parameters = {}
    for line in lines[1:]:
        match = re.fullmatch(r"[^ ]+ ([a-z_]+)=(.+)", line)
        if match is None:
            break
        parameters[match[1]] = json.loads(match[2])
    return parameters


def _training_args() -> list[str]:
    return [
        "--train-start", "2026-07-01", "--train-end", "2026-07-20",
        "--model", "sgd_regression", "--feature-group", "daily_basic_selected_v1",
        "--label-group", "daily_close_return_rank_d1/v1",
    ]


@pytest.mark.parametrize("command", ["train", "backtest"])
@pytest.mark.parametrize("experiment_id", ["run-1", "A", "model.v2_3", "a" * 64])
def test_experiment_id_option_preserves_valid_values(
    command: str, experiment_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(storage_root=tmp_path, models={}, feature_groups={})
    workflow_name = "run_offline_training" if command == "train" else "run_signal_evaluation"
    workflow = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=config))
    monkeypatch.setattr(cli.DateTimeUtils, "today", Mock(return_value="2026-10-11"))
    monkeypatch.setattr(cli, workflow_name, workflow)
    arguments = _training_args() if command == "train" else [
        "--mode", "signal_eval", "--input-json",
        '{"start":"2026-07-01","end":"2026-07-02","min_listing_calendar_days":120,"signal":{"type":"recorded","prediction_refs":[]}}',
    ]

    result = CliRunner().invoke(cli.app, [command, *arguments, f"--experiment-id={experiment_id}"])

    assert result.exit_code == 0, result.output
    workflow.assert_called_once()
    if command == "train":
        assert workflow.call_args.kwargs["experiment_id"] == experiment_id
    else:
        assert workflow.call_args.kwargs["experiment_name"] == f"backtest_2026-10-11_{experiment_id}"


@pytest.mark.parametrize("command", ["train", "backtest"])
@pytest.mark.parametrize("experiment_id", ["", "-run", " run", "run/1", "a" * 65])
def test_experiment_id_option_rejects_invalid_values_before_command_execution(
    command: str, experiment_id: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_submission = Mock()
    load_config = Mock()
    workflow = Mock()
    create_name = "create_training_submission" if command == "train" else "create_backtest_submission"
    workflow_name = "run_offline_training" if command == "train" else "run_signal_evaluation"
    monkeypatch.setattr(cli, create_name, create_submission)
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    monkeypatch.setattr(cli, workflow_name, workflow)
    arguments = _training_args() if command == "train" else [
        "--mode", "signal_eval", "--input-json",
        '{"start":"2026-07-01","end":"2026-07-02","min_listing_calendar_days":120,"signal":{"type":"recorded","prediction_refs":[]}}',
    ]

    result = CliRunner().invoke(cli.app, [command, *arguments, f"--experiment-id={experiment_id}"])

    assert result.exit_code == 2, result.output
    assert "--experiment-id" in result.output
    assert "must match" in result.output
    create_submission.assert_not_called()
    load_config.assert_not_called()
    workflow.assert_not_called()


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
    backtest_log: Path,
) -> None:
    load_config = Mock(return_value=SimpleNamespace(storage_root=tmp_path))
    today = Mock(return_value="2026-10-11")
    run = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    monkeypatch.setattr(cli.DateTimeUtils, "today", today)
    monkeypatch.setattr(cli, workflow, run)
    fields = backtest_payload(mode)
    if mode in ("signal_eval", "strategy_eval", "full_backtest"):
        fields["min_listing_calendar_days"] = 90
    del fields["kind"], fields["mode"]
    result = CliRunner().invoke(cli.app, ["backtest", "--mode", mode,
        "--experiment-id", "run-1", "--input-json", json.dumps(fields)])
    assert result.exit_code == 0, result.output
    load_config.assert_called_once_with()
    today.assert_called_once_with()
    run.assert_called_once()
    args = run.call_args.kwargs
    assert args["submission"].mode.value == mode
    assert args["experiment_name"] == "backtest_2026-10-11_run-1"
    assert "experiment_id" not in args
    assert isinstance(args["path_manager"], PathManager)
    metrics = args["metrics"]
    expected = args["submission"].model_dump(mode="json")
    assert metrics.mode.value == mode
    assert metrics.not_run == {}
    assert "backtest_config" not in args
    assert "backtest_config" not in expected
    if mode in ("signal_eval", "strategy_eval", "full_backtest"):
        assert expected["min_listing_calendar_days"] == 90
    else:
        assert "min_listing_calendar_days" not in expected
    assert metrics.parameters == expected
    assert _logged_parameters(backtest_log, experiment_name=args["experiment_name"]) == expected
    assert "observations" not in backtest_log.read_text()
    assert not args["path_manager"].experiment_dir(experiment_name=args["experiment_name"]).exists()


@pytest.mark.parametrize("missing", [None, "slippage_bp", "quote"])
def test_strategy_backtest_validates_price_model_before_running(
    missing: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    backtest_payload: Callable[[str], dict[str, object]], backtest_log: Path,
) -> None:
    load_config = Mock(return_value=SimpleNamespace(storage_root=tmp_path))
    run = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    monkeypatch.setattr(cli.DateTimeUtils, "today", Mock(return_value="2026-10-11"))
    monkeypatch.setattr(cli, "run_strategy_evaluation", run)
    fields = backtest_payload("strategy_eval")
    del fields["kind"], fields["mode"]
    if missing != "slippage_bp":
        fields["execution"]["slippage_bp"] = 0
    if missing != "quote":
        fields["strategy"]["quote"] = {"buy_offset_bp": 10, "sell_offset_bp": 0}

    result = CliRunner().invoke(cli.app, ["backtest", "--mode", "strategy_eval",
        "--experiment-id", "strategy-price", "--input-json", json.dumps(fields)])

    if missing is not None:
        assert result.exit_code == 2, result.output
        assert "execution.slippage_bp" in result.output and "strategy.quote" in result.output
        load_config.assert_not_called()
        run.assert_not_called()
        return
    assert result.exit_code == 0, result.output
    load_config.assert_called_once_with()
    run.assert_called_once()
    args = run.call_args.kwargs
    assert args["submission"].execution.slippage_bp == 0
    assert args["submission"].strategy.quote.buy_offset_bp == 10
    assert args["metrics"].parameters == args["submission"].model_dump(mode="json")
    assert _logged_parameters(backtest_log, experiment_name=args["experiment_name"]) == args["metrics"].parameters


@pytest.mark.parametrize("mode", ["signal_eval", "strategy_eval", "full_backtest"])
@pytest.mark.parametrize("listing_fields", [{}, {"min_listing_calendar_days": -1}])
def test_backtest_rejects_missing_or_invalid_listing_age_before_loading_config(
    mode: str, listing_fields: dict[str, object], monkeypatch: pytest.MonkeyPatch,
    backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    load_config = Mock()
    monkeypatch.setattr(cli.AppConfig, "load", load_config)
    fields = backtest_payload(mode)
    del fields["kind"], fields["mode"], fields["min_listing_calendar_days"]
    fields.update(listing_fields)

    result = CliRunner().invoke(cli.app, ["backtest", "--mode", mode,
        "--experiment-id", "listing-age", "--input-json", json.dumps(fields)])

    assert result.exit_code == 2, result.output
    assert "min_listing_calendar_days" in result.output
    load_config.assert_not_called()


@pytest.mark.parametrize(("current_time", "run_date"), [
    (datetime(2026, 10, 10, 15, 59, tzinfo=UTC), "2026-10-10"),
    (datetime(2026, 10, 10, 16, 0, tzinfo=UTC), "2026-10-11"),
])
def test_backtest_preserves_start_parameters_in_the_persisted_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backtest_log: Path,
    current_time: datetime, run_date: str,
) -> None:
    monkeypatch.setattr(cli.DateTimeUtils, "now_utc", lambda: current_time)
    monkeypatch.setattr(cli.AppConfig, "load", Mock(return_value=SimpleNamespace(storage_root=tmp_path)))
    experiment_id = "9c5a9280-922e-4d1a-af01-ce896eb9668c"
    fields = {"case_refs": [], "risk": {"rules": [{"type": "max_single_name", "max_ratio": 0.2}]}}
    result = CliRunner().invoke(cli.app, ["backtest", "--mode", "risk_rule_eval",
        "--experiment-id", experiment_id, "--input-json", json.dumps(fields)])
    assert result.exit_code == 0, result.output
    pm = PathManager(tmp_path)
    name = f"backtest_{run_date}_{experiment_id}"
    metrics = json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())
    assert metrics["risk_eval"]["case_count"] == 0
    assert metrics["parameters"] == _logged_parameters(backtest_log, experiment_name=name)
    assert "backtest_config" not in metrics["parameters"]
    assert pm.experiment_backtest_report(experiment_name=name).is_file()


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
                "--input-json", '{"start":"2026-07-01","end":"2026-07-02","min_listing_calendar_days":120,"signal":{"type":"recorded","prediction_refs":[]}}',
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
    backtest_log: Path,
) -> None:
    monkeypatch.setattr(cli.DateTimeUtils, "today", Mock(return_value="2026-10-11"))
    monkeypatch.setattr(
        cli.AppConfig,
        "load",
        Mock(
            return_value=SimpleNamespace(
                storage_root=tmp_path,
                models=object(), feature_groups=object(),
            )
        ),
    )
    error = ValueError("empty schedule")
    monkeypatch.setattr(
        cli,
        workflow_name,
        Mock(side_effect=error),
    )

    result = CliRunner().invoke(cli.app, arguments)

    assert result.exit_code == 1
    assert result.exception is error
    if arguments[0] == "backtest":
        name = "backtest_2026-10-11_run-1"
        parameters = _logged_parameters(backtest_log, experiment_name=name)
        assert parameters["signal"] == {"type": "recorded", "prediction_refs": []}
        assert parameters["min_listing_calendar_days"] == 120
        assert "✅ 回测报告" not in backtest_log.read_text()
        assert not PathManager(tmp_path).experiment_dir(experiment_name=name).exists()


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
