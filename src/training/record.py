# filepath: src/training/record.py
"""Describe resolved training inputs without claiming historical availability."""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from src.config.model_config import ModelConfig
from src.utils.datetime_utils import DateTimeUtils


class TrainingRunRecord(BaseModel):
    """Persist selected inputs and the label's theoretical maturity lower bound.

    The original JSON request is archival; consumers use resolved fields.
    A planned model_ref does not establish that model publication succeeded.

    Example:
        record = TrainingRunRecord(
            request=request_json, train_dates=("2026-07-01",), eval_dates=(),
            model_ref=Path("/data/run/training/model.joblib"),
            configuration=resolved_config, label_maturity_ts_us=1782975600000000,
        )
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: dict[str, JsonValue]
    train_dates: tuple[str, ...] = Field(min_length=1)
    eval_dates: tuple[str, ...]
    model_ref: Path
    configuration: ModelConfig
    label_maturity_ts_us: int = Field(strict=True)

    @model_validator(mode="after")
    def _require_record(self) -> Self:
        if not self.model_ref.is_absolute():
            raise ValueError("model_ref must be absolute")
        for dates in (self.train_dates, self.eval_dates):
            for day in dates:
                DateTimeUtils.require_system_date(day, field_name="sample_date")
            if any(left >= right for left, right in zip(dates, dates[1:])):
                raise ValueError("recorded sample dates must be unique and increasing")
        DateTimeUtils.from_utc_epoch_us(self.label_maturity_ts_us)
        return self
