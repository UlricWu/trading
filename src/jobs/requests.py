# filepath: src/jobs/requests.py
"""Construct validated workflow submissions from CLI and HTTP input."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

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
_TRAINING_FIELDS = frozenset({"kind", "windows"})
_BACKTEST_FIELDS = frozenset(
    {"kind", "mode", "start", "end", "model_ref", "model_use", "strategy"}
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
class Stock1430BackfillSubmission:
    """Describe one validated CLI-only H03 Feature/Label target range.

    Example:
        submission = Stock1430BackfillSubmission(
            start="2026-05-06",
            end="2026-05-06",
        )
    """

    start: str
    end: str


@dataclass(frozen=True, slots=True)
class Stock1430FusionBackfillSubmission:
    """Describe one validated CLI-only H04 Feature target range.

    Example:
        submission = Stock1430FusionBackfillSubmission(
            start="2026-05-06", end="2026-05-06"
        )
    """

    start: str
    end: str


class LabelAvailability(BaseModel):
    """Task-supplied availability fact for one selected label partition.

    Example:
        fact = LabelAvailability(available_ts_us=1782976200000000, evidence_ref="ledger/label-2026-07-01")
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    available_ts_us: int
    evidence_ref: str = Field(min_length=1)


class TrainingSelection(BaseModel):
    """Explicit training dates, information cutoff, and optional prediction timing.

    Availability facts are supplied by the task, rather than inferred from Meta.

    Example:
        selection = TrainingSelection(
            train_dates=("2026-07-01",), training_cutoff_ts_us=1783062600000000,
            label_availability={"2026-07-01": LabelAvailability(
                available_ts_us=1782976200000000, evidence_ref="ledger/label-2026-07-01",
            )},
        )
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    train_dates: tuple[str, ...] = Field(min_length=1, strict=False)
    training_cutoff_ts_us: int
    label_availability: dict[str, LabelAvailability]
    eval_date: str | None = None
    decision_ts_us: int | None = None

    @model_validator(mode="after")
    def _require_selection(self) -> Self:
        for trade_date in self.train_dates:
            DateTimeUtils.require_system_date(trade_date, field_name="train_dates")
        if any(left >= right for left, right in zip(self.train_dates, self.train_dates[1:])):
            raise ValueError("train_dates must be unique and increasing")
        if set(self.label_availability) != set(self.train_dates):
            raise ValueError("label_availability must cover exactly train_dates")
        DateTimeUtils.from_utc_epoch_us(self.training_cutoff_ts_us)
        for fact in self.label_availability.values():
            DateTimeUtils.from_utc_epoch_us(fact.available_ts_us)
            if not fact.evidence_ref.strip():
                raise ValueError("availability evidence_ref must not be blank")
        if (self.eval_date is None) != (self.decision_ts_us is None):
            raise ValueError("eval_date and decision_ts_us must be supplied together")
        if self.eval_date is not None:
            DateTimeUtils.require_system_date(self.eval_date, field_name="eval_date")
            local = DateTimeUtils.to_local(DateTimeUtils.from_utc_epoch_us(self.decision_ts_us))
            if local.date().isoformat() != self.eval_date:
                raise ValueError("decision_ts_us must fall on eval_date in Asia/Shanghai")
            if self.training_cutoff_ts_us > self.decision_ts_us:
                raise ValueError("training cutoff must not follow the prediction decision")
        return self


@dataclass(frozen=True, slots=True)
class TrainingSubmission:
    """Describe one explicit ordered schedule, without implicit window defaults.

    Example:
        submission = TrainingSubmission(windows=(training_selection,))
    """

    windows: tuple[TrainingSelection, ...]
    kind: Literal["train"] = "train"

    @property
    def start(self) -> str:
        """Return the first selected training or evaluation date for the range label.

        Example:
            first_date = submission.start
        """
        return min(
            min(window.train_dates[0], window.eval_date or window.train_dates[0])
            for window in self.windows
        )

    @property
    def end(self) -> str:
        """Return the last selected training or evaluation date for the range label.

        Example:
            last_date = submission.end
        """
        return max(
            max(window.train_dates[-1], window.eval_date or window.train_dates[-1])
            for window in self.windows
        )


class ModelUse(BaseModel):
    """Declare the training record and historical basis for a fixed model use.

    Example:
        use = ModelUse(
            training_schedule_ref=Path("/data/training/schedule.json"),
            effective_ts_us=1783047600000000,
            availability_evidence_ref="research/conditional-model-availability",
        )
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    training_schedule_ref: Path = Field(strict=False)
    effective_ts_us: int
    availability_evidence_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_use(self) -> Self:
        if not self.training_schedule_ref.is_absolute():
            raise ValueError("training_schedule_ref must be absolute")
        if not self.availability_evidence_ref.strip():
            raise ValueError("availability_evidence_ref must not be blank")
        DateTimeUtils.from_utc_epoch_us(self.effective_ts_us)
        return self


@dataclass(frozen=True, slots=True)
class BacktestSubmission:
    """Describe one validated full-range backtest workflow execution.

    Example:
        submission = BacktestSubmission(
            mode=BacktestMode.FULL_BACKTEST,
            start="2026-07-01",
            end="2026-07-20",
            model_ref=Path("/data/models/training-1.joblib"),
            model_use=use,
            strategy=threshold_strategy,
        )
    """

    mode: BacktestMode
    start: str
    end: str
    model_ref: Path
    model_use: ModelUse
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


def create_stock_1430_backfill_submission(
    start: object,
    end: object,
) -> Stock1430BackfillSubmission:
    """Construct one usable CLI-only H03 Feature/Label submission.

    Example:
        submission = create_stock_1430_backfill_submission(
            "2026-05-06",
            "2026-05-06",
        )
    """
    normalized_start, normalized_end = _require_range(start, end)
    return Stock1430BackfillSubmission(
        start=normalized_start,
        end=normalized_end,
    )


def create_stock_1430_fusion_backfill_submission(
    start: object,
    end: object,
) -> Stock1430FusionBackfillSubmission:
    """Construct one usable CLI-only H04 Feature submission.

    Example:
        submission = create_stock_1430_fusion_backfill_submission(
            "2026-05-06", "2026-05-06"
        )
    """
    normalized_start, normalized_end = _require_range(start, end)
    return Stock1430FusionBackfillSubmission(start=normalized_start, end=normalized_end)


def create_training_submission(windows: object) -> TrainingSubmission:
    """Validate one explicit schedule shared by CLI and HTTP.

    Example:
        submission = create_training_submission([{
            "train_dates": ["2026-07-01"], "training_cutoff_ts_us": 1783062600000000,
            "label_availability": {"2026-07-01": {
                "available_ts_us": 1782976200000000, "evidence_ref": "ledger/label-2026-07-01",
            }},
        }])
    """
    try:
        selections = TypeAdapter(list[TrainingSelection]).validate_python(windows)
    except (ValidationError, TypeError, ValueError) as exc:
        raise InvalidJobRequest("must be a valid explicit training schedule", field="windows") from exc
    if not selections:
        raise InvalidJobRequest("must not be empty", field="windows")
    decision_times = [item.decision_ts_us for item in selections if item.decision_ts_us is not None]
    if any(left >= right for left, right in zip(decision_times, decision_times[1:])):
        raise InvalidJobRequest("evaluation decision times must be unique and increasing", field="windows")
    evaluation_dates = [item.eval_date for item in selections if item.eval_date is not None]
    if any(left >= right for left, right in zip(evaluation_dates, evaluation_dates[1:])):
        raise InvalidJobRequest("daily evaluation dates must be unique and increasing", field="windows")
    return TrainingSubmission(windows=tuple(selections))


def create_backtest_submission(
    *,
    mode: object,
    start: object,
    end: object,
    model_ref: object,
    model_use: object,
    strategy: object,
) -> BacktestSubmission:
    """Construct one usable full-range backtest submission.

    Example:
        submission = create_backtest_submission(
            mode="full_backtest",
            start="2026-07-01",
            end="2026-07-20",
            model_ref="/data/models/training-1.joblib",
            model_use=use,
            strategy={"type": "threshold", "params": {"threshold": 0.5}},
        )
    """
    normalized_start, normalized_end = _require_range(start, end)

    try:
        normalized_mode = BacktestMode(mode)
    except (TypeError, ValueError) as exc:
        raise InvalidJobRequest("is invalid", field="mode") from exc

    if not isinstance(model_ref, str | Path):
        raise InvalidJobRequest("must be an absolute file path", field="model_ref")
    normalized_model_ref = Path(model_ref)
    if not normalized_model_ref.is_absolute():
        raise InvalidJobRequest("must be an absolute file path", field="model_ref")

    try:
        normalized_use = ModelUse.model_validate(model_use)
    except ValidationError as exc:
        raise InvalidJobRequest("must declare a valid model use", field="model_use") from exc

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
        model_ref=normalized_model_ref,
        model_use=normalized_use,
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
            create_training_submission(request_payload.get("windows"))
        ]

    _reject_unknown_fields(request_payload, _BACKTEST_FIELDS)
    return [
        create_backtest_submission(
            mode=request_payload.get("mode"),
            start=request_payload.get("start"),
            end=request_payload.get("end"),
            model_ref=request_payload.get("model_ref"),
            model_use=request_payload.get("model_use"),
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
                "--windows-json",
                json.dumps([window.model_dump(mode="json") for window in submission.windows],
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")),
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
                "--model-ref",
                str(submission.model_ref),
                "--model-use-json",
                submission.model_use.model_dump_json(),
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
