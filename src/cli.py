# filepath: src/cli.py
"""Define the public CLI composition roots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from src.config.app_config import AppConfig
from src.jobs.requests import (
    InvalidJobRequest,
    TrainingDateRange,
    create_backtest_submission,
    create_data_submission,
    create_feature_backfill_submission,
    create_level2_minute_backfill_submission,
    create_standard_fact_bootstrap_submission,
    create_stock_1430_backfill_submission,
    create_stock_1430_fusion_backfill_submission,
    create_training_submission,
)
from src.utils.datetime_utils import DateTimeUtils
from src.utils.logger import configure_cli_logging
from src.utils.path import PathManager
from src.workflows.backtest import (
    run_signal_evaluation, run_risk_rule_evaluation, run_execution_evaluation,
    run_strategy_evaluation, run_full_backtest,
)
from src.jobs.requests import (
    SignalEvalSubmission, RiskRuleEvalSubmission, ExecutionEvalSubmission, StrategyEvalSubmission,
)
from src.workflows.offline_daily_data import (
    run_feature_backfill,
    run_level2_minute_backfill,
    run_offline_data,
    run_standard_fact_bootstrap,
    run_stock_1430_backfill,
    run_stock_1430_fusion_backfill,
    run_previous_day_backfill,
)
from src.workflows.offline_training import run_offline_training
from src.workflows.trade_calendar import run_trade_calendar_bootstrap

app = typer.Typer(help="MinQuant CLI")


def _require_experiment_id(value: str) -> str:
    try:
        return PathManager.require_experiment_id(value)
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--experiment-id") from None


def _raise_bad_parameter(
    error: InvalidJobRequest,
    field_hints: Mapping[str, str],
) -> NoReturn:
    hint = field_hints.get(error.field) if error.field is not None else None
    raise typer.BadParameter(str(error), param_hint=hint) from None


@app.command()
def data_calendar() -> None:
    """Bootstrap annual trade calendars from 2016 through the current year.

    Example:
        data_calendar()
    """
    app_config = AppConfig.load()
    run_trade_calendar_bootstrap(
        app_config=app_config,
        path_manager=PathManager(app_config.storage_root),
        as_of_date=DateTimeUtils.today(),
    )


@app.command()
def data_standard_bootstrap(
    start_date: str = typer.Option(..., "--start"),
    end_date: str = typer.Option(..., "--end"),
) -> None:
    """Materialize one explicit Standard calendar and fact cold-start range.

    Example:
        data_standard_bootstrap(
            start_date="2019-01-01",
            end_date="2019-04-03",
        )
    """
    try:
        submission = create_standard_fact_bootstrap_submission(
            start_date,
            end_date,
        )
    except InvalidJobRequest as exc:
        _raise_bad_parameter(
            exc,
            {"start": "--start", "end": "--end"},
        )

    app_config = AppConfig.load()
    run_standard_fact_bootstrap(
        app_config=app_config,
        path_manager=PathManager(app_config.storage_root),
        submission=submission,
    )


@app.command()
def data_standard(
    start_date: str = typer.Option(..., "--start"),
    end_date: str = typer.Option(..., "--end"),
) -> None:
    """Run the standard offline data workflow for one inclusive range.

    Example:
        data_standard(
            start_date="2026-07-01",
            end_date="2026-07-20",
        )
    """
    try:
        submission = create_data_submission(
            "data-standard",
            start_date,
            end_date,
        )
    except InvalidJobRequest as exc:
        _raise_bad_parameter(
            exc,
            {"start": "--start", "end": "--end"},
        )

    app_config = AppConfig.load()
    run_offline_data(
        app_config=app_config,
        path_manager=PathManager(app_config.storage_root),
        submission=submission,
    )


@app.command()
def data_level2(
    start_date: str = typer.Option(..., "--start"),
    end_date: str = typer.Option(..., "--end"),
) -> None:
    """Run the Level-2 offline data workflow for one inclusive range.

    Example:
        data_level2(
            start_date="2026-07-01",
            end_date="2026-07-20",
        )
    """
    try:
        submission = create_data_submission(
            "data-level2",
            start_date,
            end_date,
        )
    except InvalidJobRequest as exc:
        _raise_bad_parameter(
            exc,
            {"start": "--start", "end": "--end"},
        )

    app_config = AppConfig.load()
    run_offline_data(
        app_config=app_config,
        path_manager=PathManager(app_config.storage_root),
        submission=submission,
    )


@app.command()
def data_level2_minute_backfill(
    start_date: str = typer.Option(..., "--start"),
    end_date: str = typer.Option(..., "--end"),
) -> None:
    """Backfill both Level2 stock minute facts over one target range.

    Example:
        data_level2_minute_backfill(
            start_date="2025-11-18",
            end_date="2025-11-18",
        )
    """
    try:
        submission = create_level2_minute_backfill_submission(
            start_date,
            end_date,
        )
    except InvalidJobRequest as exc:
        _raise_bad_parameter(
            exc,
            {"start": "--start", "end": "--end"},
        )

    app_config = AppConfig.load()
    run_level2_minute_backfill(
        path_manager=PathManager(app_config.storage_root),
        submission=submission,
    )


@app.command()
def data_stock_1430_backfill(
    start_date: str = typer.Option(..., "--start"),
    end_date: str = typer.Option(..., "--end"),
) -> None:
    """Backfill the fixed H03 14:30 Feature and T+1 Label pair.

    Example:
        data_stock_1430_backfill(
            start_date="2026-05-06",
            end_date="2026-05-06",
        )
    """
    try:
        submission = create_stock_1430_backfill_submission(
            start_date,
            end_date,
        )
    except InvalidJobRequest as exc:
        _raise_bad_parameter(
            exc,
            {"start": "--start", "end": "--end"},
        )

    app_config = AppConfig.load()
    run_stock_1430_backfill(
        path_manager=PathManager(app_config.storage_root),
        submission=submission,
    )


@app.command()
def data_stock_1430_fusion_backfill(
    start_date: str = typer.Option(..., "--start"),
    end_date: str = typer.Option(..., "--end"),
) -> None:
    """Backfill the fixed H04 daily/L2 Feature over target sessions.

    Example:
        data_stock_1430_fusion_backfill(
            start_date="2026-05-06", end_date="2026-05-06"
        )
    """
    try:
        submission = create_stock_1430_fusion_backfill_submission(start_date, end_date)
    except InvalidJobRequest as exc:
        _raise_bad_parameter(exc, {"start": "--start", "end": "--end"})

    app_config = AppConfig.load()
    run_stock_1430_fusion_backfill(
        path_manager=PathManager(app_config.storage_root),
        submission=submission,
    )


@app.command()
def data_feature_backfill(
    feature_set: str = typer.Option(..., "--feature-set"),
    version: str = typer.Option(..., "--version"),
    start_date: str = typer.Option(..., "--start"),
    end_date: str = typer.Option(..., "--end"),
) -> None:
    """Backfill one exact Feature identity over one target range.

    Example:
        data_feature_backfill(
            feature_set="tushare_daily_basic",
            version="v1",
            start_date="2019-04-04",
            end_date="2019-07-05",
        )
    """
    try:
        submission = create_feature_backfill_submission(
            feature_set=feature_set,
            version=version,
            start=start_date,
            end=end_date,
        )
    except InvalidJobRequest as exc:
        _raise_bad_parameter(
            exc,
            {
                "feature_set": "--feature-set",
                "version": "--version",
                "start": "--start",
                "end": "--end",
            },
        )

    app_config = AppConfig.load()
    run_feature_backfill(
        path_manager=PathManager(app_config.storage_root),
        submission=submission,
    )


@app.command()
def data_previous_day_backfill(
    start_date: str = typer.Option(..., "--start"), end_date: str = typer.Option(..., "--end"),
) -> None:
    """Prepare preopen training inputs from committed local market data.

    Example:
        data_previous_day_backfill(start_date="2026-07-01", end_date="2026-07-31")
    """
    try:
        interval = TrainingDateRange(start=start_date, end=end_date)
    except ValidationError as exc:
        raise typer.BadParameter(exc.errors(include_input=False)[0]["msg"], param_hint="--start/--end") from None
    config = AppConfig.load()
    run_previous_day_backfill(path_manager=PathManager(config.storage_root), interval=interval)


@app.command()
def train(
    train_start: str = typer.Option(..., "--train-start"),
    train_end: str = typer.Option(..., "--train-end"),
    model: str = typer.Option(..., "--model"),
    feature_group: str = typer.Option(..., "--feature-group"),
    label_group: str = typer.Option(..., "--label-group"),
    experiment_id: str = typer.Option(..., "--experiment-id"),
    eval_start: str | None = typer.Option(None, "--eval-start"),
    eval_end: str | None = typer.Option(None, "--eval-end"),
    eval_gap_sessions: Annotated[int, typer.Option("--eval-gap-sessions")] = 0,
    validation_sessions: Annotated[int, typer.Option("--validation-sessions")] = 0,
) -> None:
    """Train one selected model and optionally evaluate it over a date range.

    Example:
        train(
            train_start="2026-07-01", train_end="2026-07-31",
            model="sgd_regression", feature_group="daily_basic_selected_v1",
            label_group="daily_close_return_rank_d1/v1", experiment_id="training-1",
            eval_start="2026-08-10", eval_end="2026-08-31",
        )
    """
    try:
        submission = create_training_submission(
            train_range={"start": train_start, "end": train_end},
            eval_range=(
                {"start": eval_start, "end": eval_end}
                if eval_start is not None or eval_end is not None else None
            ),
            model=model,
            dataset={"feature_group": feature_group, "label_group": label_group},
            eval_gap_sessions=eval_gap_sessions,
            validation_sessions=validation_sessions,
        )
    except InvalidJobRequest as exc:
        _raise_bad_parameter(exc, {
            "train_range": "--train-start/--train-end",
            "train_range.start": "--train-start", "train_range.end": "--train-end",
            "eval_range": "--eval-start/--eval-end",
            "eval_range.start": "--eval-start", "eval_range.end": "--eval-end",
            "eval_gap_sessions": "--eval-gap-sessions", "validation_sessions": "--validation-sessions",
            "model": "--model", "dataset": "--feature-group/--label-group",
            "dataset.feature_group": "--feature-group", "dataset.label_group": "--label-group",
        })
    experiment_id = _require_experiment_id(experiment_id)
    app_config = AppConfig.load()
    run_offline_training(
        models=app_config.models,
        feature_groups=app_config.feature_groups,
        path_manager=PathManager(app_config.storage_root),
        submission=submission, experiment_id=experiment_id,
    )


@app.command()
def backtest(
    mode: str = typer.Option(..., "--mode"),
    experiment_id: str = typer.Option(..., "--experiment-id"),
    input_json: str = typer.Option(..., "--input-json"),
) -> None:
    """Run the selected evaluation using only its own validated input fields.

    Example:
        backtest(mode="signal_eval", experiment_id="signal-1",
                 input_json='{"start":"2026-07-03","end":"2026-07-03","signal":{"type":"recorded","prediction_refs":[]}}')
    """
    experiment_id = _require_experiment_id(experiment_id)
    try:
        fields = json.loads(input_json)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("must be valid JSON", param_hint="--input-json") from exc
    if not isinstance(fields, dict) or "mode" in fields or "kind" in fields:
        raise typer.BadParameter("must be a task input object without mode/kind", param_hint="--input-json")
    try:
        submission = create_backtest_submission(mode=mode, **fields)
    except InvalidJobRequest as exc:
        raise typer.BadParameter(f"{exc.field}: {exc}", param_hint="--input-json") from exc
    app_config = AppConfig.load()
    pm = PathManager(app_config.storage_root)
    if isinstance(submission, SignalEvalSubmission):
        run_signal_evaluation(submission=submission, path_manager=pm,
                              experiment_id=experiment_id, backtest_config=app_config.backtest)
    elif isinstance(submission, RiskRuleEvalSubmission):
        run_risk_rule_evaluation(submission=submission, path_manager=pm, experiment_id=experiment_id)
    elif isinstance(submission, ExecutionEvalSubmission):
        run_execution_evaluation(submission=submission, path_manager=pm, experiment_id=experiment_id)
    elif isinstance(submission, StrategyEvalSubmission):
        run_strategy_evaluation(submission=submission, path_manager=pm,
                                experiment_id=experiment_id, backtest_config=app_config.backtest)
    else:
        run_full_backtest(submission=submission, path_manager=pm,
                          experiment_id=experiment_id, backtest_config=app_config.backtest)


if __name__ == "__main__":
    configure_cli_logging()
    app()
