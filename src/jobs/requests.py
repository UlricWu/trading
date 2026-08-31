# filepath: src/jobs/requests.py
"""Construct validated workflow submissions from CLI and HTTP input."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias, cast

from pydantic import TypeAdapter, ValidationError

from src.config.backtest_config import BacktestMode, StrategyConfig
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


DataJobKind: TypeAlias = Literal["data-standard", "data-level2"]
JobKind: TypeAlias = Literal[
    "data-standard",
    "data-level2",
    "train",
    "backtest",
]

JOB_EXIT_CODE_SKIPPED = 75

_DATA_KINDS = frozenset({"data-standard", "data-level2"})
_JOB_KINDS = _DATA_KINDS | {"train", "backtest"}
_DATA_FIELDS = frozenset({"kind", "start", "end"})
_TRAINING_FIELDS = frozenset({"kind", "start", "end"})
_BACKTEST_FIELDS = frozenset(
    {"kind", "mode", "start", "end", "model_experiment", "strategy"}
)


class InvalidJobRequest(ValueError):
    """Report one invalid public workflow field without exposing implementation data.

    Example:
        error = InvalidJobRequest("is required", field="date")
        assert error.field == "date"
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True, slots=True)
class DataSubmission:
    """Describe one validated full-range data workflow execution.

    Example:
        submission = DataSubmission(
            kind="data-standard",
            start="2026-07-20",
            end="2026-07-20",
        )
    """

    kind: DataJobKind
    start: str
    end: str


@dataclass(frozen=True, slots=True)
class StandardFactBootstrapSubmission:
    """Describe one validated CLI-only Standard fact cold-start range.

    Example:
        submission = StandardFactBootstrapSubmission(
            start="2019-01-01",
            end="2019-04-03",
        )
    """

    start: str
    end: str


@dataclass(frozen=True, slots=True)
class FeatureBackfillSubmission:
    """Describe one validated CLI-only Feature target range.

    Example:
        submission = FeatureBackfillSubmission(
            feature_set="tushare_daily_basic",
            version="v1",
            start="2019-04-04",
            end="2019-07-05",
        )
    """

    feature_set: str
    version: str
    start: str
    end: str


@dataclass(frozen=True, slots=True)
class Level2MinuteBackfillSubmission:
    """Describe one validated CLI-only Level2 minute target range.

    Example:
        submission = Level2MinuteBackfillSubmission(
            start="2025-11-18",
            end="2025-11-18",
        )
    """

    start: str
    end: str


@dataclass(frozen=True, slots=True)
class TrainingSubmission:
    """Describe one validated full-range training workflow execution.

    Example:
        submission = TrainingSubmission(start="2026-07-01", end="2026-07-20")
    """

    start: str
    end: str
    kind: Literal["train"] = "train"


@dataclass(frozen=True, slots=True)
class BacktestSubmission:
    """Describe one validated full-range backtest workflow execution.

    Example:
        submission = create_backtest_submission(
            mode="full_backtest",
            start="2026-07-01",
            end="2026-07-20",
            model_experiment="training-1",
            strategy={"type": "threshold", "params": {"threshold": 0.5}},
        )
    """

    mode: BacktestMode
    start: str
    end: str
    model_experiment: str
    strategy: StrategyConfig
    kind: Literal["backtest"] = "backtest"


JobSubmission: TypeAlias = DataSubmission | TrainingSubmission | BacktestSubmission


def create_data_submission(
    kind: object,
    start: object,
    end: object,
) -> DataSubmission:
    """Construct one usable full-range data submission.

    Example:
        submission = create_data_submission(
            "data-standard",
            "2026-07-01",
            "2026-07-20",
        )
    """
    if kind not in _DATA_KINDS:
        raise InvalidJobRequest(
            "must be data-standard or data-level2",
            field="kind",
        )
    normalized_start, normalized_end = _require_range(start, end)
    return DataSubmission(
        kind=cast(DataJobKind, kind),
        start=normalized_start,
        end=normalized_end,
    )


def create_standard_fact_bootstrap_submission(
    start: object,
    end: object,
) -> StandardFactBootstrapSubmission:
    """Construct one usable CLI-only Standard fact cold-start submission.

    Example:
        submission = create_standard_fact_bootstrap_submission(
            "2019-01-01",
            "2019-04-03",
        )
    """
    normalized_start, normalized_end = _require_range(start, end)
    return StandardFactBootstrapSubmission(
        start=normalized_start,
        end=normalized_end,
    )


def create_feature_backfill_submission(
    *,
    feature_set: object,
    version: object,
    start: object,
    end: object,
) -> FeatureBackfillSubmission:
    """Construct one usable CLI-only Feature backfill submission.

    Example:
        submission = create_feature_backfill_submission(
            feature_set="tushare_daily_basic",
            version="v1",
            start="2019-04-04",
            end="2019-07-05",
        )
    """
    normalized_feature_set = _require_safe_basename(
        feature_set,
        field="feature_set",
    )
    normalized_version = _require_safe_basename(version, field="version")
    normalized_start, normalized_end = _require_range(start, end)
    return FeatureBackfillSubmission(
        feature_set=normalized_feature_set,
        version=normalized_version,
        start=normalized_start,
        end=normalized_end,
    )


def create_level2_minute_backfill_submission(
    start: object,
    end: object,
) -> Level2MinuteBackfillSubmission:
    """Construct one usable CLI-only Level2 minute backfill submission.

    Example:
        submission = create_level2_minute_backfill_submission(
            "2025-11-18",
            "2025-11-18",
        )
    """
    normalized_start, normalized_end = _require_range(start, end)
    return Level2MinuteBackfillSubmission(
        start=normalized_start,
        end=normalized_end,
    )


def create_training_submission(
    start: object,
    end: object,
) -> TrainingSubmission:
    """Construct one usable full-range training submission.

    Example:
        submission = create_training_submission("2026-07-01", "2026-07-20")
    """
    normalized_start, normalized_end = _require_range(start, end)
    return TrainingSubmission(start=normalized_start, end=normalized_end)


def create_backtest_submission(
    *,
    mode: object,
    start: object,
    end: object,
    model_experiment: object,
    strategy: object,
) -> BacktestSubmission:
    """Construct one usable full-range backtest submission.

    Example:
        submission = create_backtest_submission(
            mode="full_backtest",
            start="2026-07-01",
            end="2026-07-20",
            model_experiment="training-1",
            strategy={"type": "threshold", "params": {"threshold": 0.5}},
        )
    """
    normalized_start, normalized_end = _require_range(start, end)

    try:
        normalized_mode = BacktestMode(mode)
    except (TypeError, ValueError) as exc:
        raise InvalidJobRequest("is invalid", field="mode") from exc

    normalized_model_experiment = _require_safe_basename(
        model_experiment,
        field="model_experiment",
    )

    try:
        normalized_strategy = TypeAdapter(StrategyConfig).validate_python(strategy)
    except ValidationError as exc:
        raise InvalidJobRequest(
            "must match a supported strategy schema",
            field="strategy",
        ) from exc

    return BacktestSubmission(
        mode=normalized_mode,
        start=normalized_start,
        end=normalized_end,
        model_experiment=normalized_model_experiment,
        strategy=normalized_strategy,
    )


def parse_job_request(payload: object) -> list[JobSubmission]:
    """Validate one HTTP request and return its complete atomic job list.

    Example:
        submissions = parse_job_request(
            {
                "kind": "data-standard",
                "start": "2026-07-01",
                "end": "2026-07-20",
            }
        )
    """
    if not isinstance(payload, Mapping):
        raise InvalidJobRequest("request body must be a JSON object")
    if not all(isinstance(field, str) for field in payload):
        raise InvalidJobRequest("request fields must be strings")

    request_payload = cast(Mapping[str, object], payload)
    kind = request_payload.get("kind")
    if not isinstance(kind, str):
        raise InvalidJobRequest("must be a string", field="kind")
    if kind not in _JOB_KINDS:
        raise InvalidJobRequest("is not supported", field="kind")

    if kind in _DATA_KINDS:
        _reject_unknown_fields(request_payload, _DATA_FIELDS)
        return [
            create_data_submission(
                kind,
                request_payload.get("start"),
                request_payload.get("end"),
            )
        ]
    if kind == "train":
        _reject_unknown_fields(request_payload, _TRAINING_FIELDS)
        return [
            create_training_submission(
                request_payload.get("start"),
                request_payload.get("end"),
            )
        ]

    _reject_unknown_fields(request_payload, _BACKTEST_FIELDS)
    return [
        create_backtest_submission(
            mode=request_payload.get("mode"),
            start=request_payload.get("start"),
            end=request_payload.get("end"),
            model_experiment=request_payload.get("model_experiment"),
            strategy=request_payload.get("strategy"),
        )
    ]


def build_cli_command(
    submission: JobSubmission,
    job_id: str,
    *,
    python_executable: Path = Path(sys.executable),
) -> tuple[str, ...]:
    """Build the only CLI command for one already validated submission.

    Example:
        command = build_cli_command(
            DataSubmission(
                kind="data-standard",
                start="2026-07-01",
                end="2026-07-20",
            ),
            "00000000-0000-4000-8000-000000000001",
        )
    """
    command = [
        str(python_executable),
        "-m",
        "src.cli",
        submission.kind,
    ]
    if isinstance(submission, DataSubmission):
        command.extend(
            [
                "--start",
                submission.start,
                "--end",
                submission.end,
            ]
        )
    elif isinstance(submission, TrainingSubmission):
        command.extend(
            [
                "--start",
                submission.start,
                "--end",
                submission.end,
                "--experiment-id",
                job_id,
            ]
        )
    else:
        command.extend(
            [
                "--mode",
                submission.mode.value,
                "--start",
                submission.start,
                "--end",
                submission.end,
                "--experiment-id",
                job_id,
                "--model-experiment",
                submission.model_experiment,
                "--strategy-json",
                json.dumps(
                    submission.strategy.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ]
        )
    return tuple(command)


def _reject_unknown_fields(
    payload: Mapping[str, object],
    allowed_fields: frozenset[str],
) -> None:
    unknown_fields = sorted(set(payload) - allowed_fields)
    if unknown_fields:
        raise InvalidJobRequest("is not allowed", field=unknown_fields[0])


def _require_date(value: object, *, field: str) -> str:
    try:
        return DateTimeUtils.require_system_date(value, field_name=field)
    except (TypeError, ValueError) as exc:
        raise InvalidJobRequest("must be a valid YYYY-MM-DD date", field=field) from exc


def _require_safe_basename(value: object, *, field: str) -> str:
    try:
        return PathManager.require_safe_basename(cast(str, value), field)
    except TypeError as exc:
        raise InvalidJobRequest("must be a string", field=field) from exc
    except ValueError as exc:
        raise InvalidJobRequest("must be a safe basename", field=field) from exc


def _require_range(start: object, end: object) -> tuple[str, str]:
    normalized_start = _require_date(start, field="start")
    normalized_end = _require_date(end, field="end")
    if normalized_start > normalized_end:
        raise InvalidJobRequest(
            "must be on or before end",
            field="start",
        )
    return normalized_start, normalized_end
