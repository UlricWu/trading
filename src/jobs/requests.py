# filepath: src/jobs/requests.py
"""Construct validated workflow submissions from CLI and HTTP input."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from src.config.backtest_config import BacktestMode, StrategyConfig
from src.config.model_config import FeatureGroupName, LabelGroupName, TrainingModelName
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
_TRAINING_FIELDS = frozenset({"kind", "train_range", "eval_range", "eval_gap_sessions", "validation_sessions", "model", "dataset"})
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


class TrainingDateRange(BaseModel):
    """Select a closed interval of sample dates using canonical market dates.

    Example:
        interval = TrainingDateRange(start="2026-07-01", end="2026-07-31")
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _require_date(cls, value: str) -> str:
        return DateTimeUtils.require_system_date(value, field_name="date")

    @model_validator(mode="after")
    def _require_range(self) -> Self:
        if self.start > self.end:
            raise ValueError("start must not follow end")
        return self


class TrainingDatasetSelection(BaseModel):
    """Choose compatible feature columns and an existing label identity.

    Example:
        selection = TrainingDatasetSelection(
            feature_group="daily_basic_selected_v1",
            label_group="daily_close_return_rank_d1/v1",
        )
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    feature_group: FeatureGroupName
    label_group: LabelGroupName

    @model_validator(mode="after")
    def _require_compatible_groups(self) -> Self:
        preopen = self.feature_group.startswith("previous_day_")
        if preopen or self.label_group.startswith("next_day_high_"):
            if preopen != self.label_group.startswith("next_day_high_"):
                raise ValueError("feature_group and label_group must use the same sample grid")
            return self
        if (self.feature_group == "l2_stock_1430_all_v1") != (
            self.label_group == "l2_stock_1430_t1_vwap_rank/v1"
        ):
            raise ValueError("feature_group and label_group must use the same sample grid")
        return self


class TrainingSubmission(BaseModel):
    """Request one model fit and optional evaluation over a closed date range.

    Example:
        submission = TrainingSubmission(
            train_range=TrainingDateRange(start="2026-07-01", end="2026-07-31"),
            model="sgd_regression", dataset=dataset_selection,
        )
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    train_range: TrainingDateRange
    model: TrainingModelName
    dataset: TrainingDatasetSelection
    eval_range: TrainingDateRange | None = None
    eval_gap_sessions: int = Field(default=0, ge=0)
    validation_sessions: int = Field(default=0, ge=0)
    kind: Literal["train"] = "train"

    @model_validator(mode="after")
    def _require_training_choices(self) -> Self:
        classifier = self.model in {"logistic_regression", "hist_gradient_boosting_classifier"}
        if classifier != (self.dataset.label_group == "next_day_high_top_decile/v1"):
            raise ValueError("classifier models require next_day_high_top_decile/v1; regressors require rank labels")
        return self

    @property
    def start(self) -> str:
        """Return the first selected training or evaluation date for the range label.

        Example:
            first_date = submission.start
        """
        return min(self.train_range.start, self.eval_range.start) if self.eval_range is not None else self.train_range.start

    @property
    def end(self) -> str:
        """Return the last selected training or evaluation date for the range label.

        Example:
            last_date = submission.end
        """
        return max(self.train_range.end, self.eval_range.end) if self.eval_range is not None else self.train_range.end


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


def create_training_submission(
    *, train_range: object, model: object, dataset: object, eval_range: object = None,
    eval_gap_sessions: object = 0, validation_sessions: object = 0,
) -> TrainingSubmission:
    """Validate the business choices shared by CLI and HTTP.

    Example:
        submission = create_training_submission(
            train_range={"start": "2026-07-01", "end": "2026-07-31"},
            model="sgd_regression", dataset={
                "feature_group": "daily_basic_selected_v1",
                "label_group": "daily_close_return_rank_d1/v1",
            },
        )
    """
    try:
        return TrainingSubmission.model_validate({
            "train_range": train_range, "eval_range": eval_range,
            "model": model, "dataset": dataset,
            "eval_gap_sessions": eval_gap_sessions,
            "validation_sessions": validation_sessions,
        })
    except ValidationError as exc:
        error = exc.errors(include_url=False, include_input=False)[0]
        field = ".".join(str(part) for part in error["loc"])
        raise InvalidJobRequest(error["msg"], field=field) from exc


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
        return [create_training_submission(
            train_range=request_payload.get("train_range"),
            eval_range=request_payload.get("eval_range"),
            eval_gap_sessions=request_payload.get("eval_gap_sessions", 0),
            validation_sessions=request_payload.get("validation_sessions", 0),
            model=request_payload.get("model"), dataset=request_payload.get("dataset"),
        )]

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
                "--train-start", submission.train_range.start,
                "--train-end", submission.train_range.end,
                "--model", submission.model,
                "--feature-group", submission.dataset.feature_group,
                "--label-group", submission.dataset.label_group,
                "--experiment-id",
                job_id,
            ]
        )
        if submission.eval_range is not None:
            command.extend([
                "--eval-start", submission.eval_range.start,
                "--eval-end", submission.eval_range.end,
            ])
        if submission.eval_gap_sessions:
            command.extend(["--eval-gap-sessions", str(submission.eval_gap_sessions)])
        if submission.validation_sessions:
            command.extend(["--validation-sessions", str(submission.validation_sessions)])
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
