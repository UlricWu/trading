# filepath: src/cli.py
"""Define the four public CLI composition roots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import NoReturn

import typer

from src.config.app_config import AppConfig
from src.data_system.pipeline import DataRunStatus
from src.jobs.requests import (
    JOB_EXIT_CODE_SKIPPED,
    InvalidJobRequest,
    create_backtest_submission,
    create_data_submission,
    create_training_submission,
)
from src.utils.path import PathManager
from src.workflows.backtest import run_daily_alpha_backtest
from src.workflows.offline_daily_data import (
    run_offline_level2_data,
    run_offline_standard_data,
)
from src.workflows.offline_training import run_offline_training


app = typer.Typer(help="MinQuant CLI")


def _require_experiment_id(value: str) -> str:
    try:
        return PathManager.require_experiment_id(value)
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--experiment-id") from None


def _parse_strategy_json(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        raise typer.BadParameter(
            "must be valid JSON",
            param_hint="--strategy-json",
        ) from None


def _raise_bad_parameter(
    error: InvalidJobRequest,
    field_hints: Mapping[str, str],
) -> NoReturn:
    hint = field_hints.get(error.field) if error.field is not None else None
    raise typer.BadParameter(str(error), param_hint=hint) from None


@app.command()  # type: ignore[untyped-decorator]
def data_standard(date: str) -> None:
    """Run the standard offline data workflow for one date."""
    try:
        submission = create_data_submission("data-standard", date)
    except InvalidJobRequest as exc:
        _raise_bad_parameter(exc, {"date": "DATE"})

    app_config = AppConfig.load()
    status = run_offline_standard_data(
        app_config=app_config,
        path_manager=PathManager(app_config.storage.root),
        trade_date=submission.date,
    )
    if status is DataRunStatus.SKIPPED:
        raise typer.Exit(code=JOB_EXIT_CODE_SKIPPED)


@app.command()  # type: ignore[untyped-decorator]
def data_level2(date: str) -> None:
    """Run the Level-2 offline data workflow for one date."""
    try:
        submission = create_data_submission("data-level2", date)
    except InvalidJobRequest as exc:
        _raise_bad_parameter(exc, {"date": "DATE"})

    app_config = AppConfig.load()
    status = run_offline_level2_data(
        app_config=app_config,
        path_manager=PathManager(app_config.storage.root),
        trade_date=submission.date,
    )
    if status is DataRunStatus.SKIPPED:
        raise typer.Exit(code=JOB_EXIT_CODE_SKIPPED)


@app.command()  # type: ignore[untyped-decorator]
def train(
    start_date: str = typer.Option(..., "--start"),
    end_date: str = typer.Option(..., "--end"),
    experiment_id: str = typer.Option(..., "--experiment-id"),
) -> None:
    """Run one offline training experiment."""
    try:
        submission = create_training_submission(start_date, end_date)
    except InvalidJobRequest as exc:
        _raise_bad_parameter(
            exc,
            {"start": "--start", "end": "--end"},
        )
    experiment_id = _require_experiment_id(experiment_id)

    app_config = AppConfig.load()
    run_offline_training(
        model_config=app_config.model,
        path_manager=PathManager(app_config.storage.root),
        experiment_id=experiment_id,
        start_date=submission.start,
        end_date=submission.end,
    )


@app.command()  # type: ignore[untyped-decorator]
def backtest(
    mode: str = typer.Option(..., "--mode"),
    start_date: str = typer.Option(..., "--start"),
    end_date: str = typer.Option(..., "--end"),
    experiment_id: str = typer.Option(..., "--experiment-id"),
    model_experiment: str = typer.Option(..., "--model-experiment"),
    strategy_json: str = typer.Option(..., "--strategy-json"),
) -> None:
    """Run one offline daily-alpha backtest experiment."""
    experiment_id = _require_experiment_id(experiment_id)
    try:
        submission = create_backtest_submission(
            mode=mode,
            start=start_date,
            end=end_date,
            model_experiment=model_experiment,
            strategy=_parse_strategy_json(strategy_json),
        )
    except InvalidJobRequest as exc:
        _raise_bad_parameter(
            exc,
            {
                "mode": "--mode",
                "start": "--start",
                "end": "--end",
                "model_experiment": "--model-experiment",
                "strategy": "--strategy-json",
            },
        )

    app_config = AppConfig.load(
        override={
            "backtest": {
                "backtest_mode": submission.mode.value,
                "model": {"name": submission.model_experiment},
                "strategy": submission.strategy.model_dump(mode="json"),
            }
        }
    )
    run_daily_alpha_backtest(
        backtest_config=app_config.backtest,
        path_manager=PathManager(app_config.storage.root),
        experiment_id=experiment_id,
        start_date=submission.start,
        end_date=submission.end,
    )


if __name__ == "__main__":
    app()
