# filepath: src/config/data_config.py
"""Pydantic models for `docs/data/source_contract.md` data configuration."""

from __future__ import annotations

from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DownloadBackend(str, Enum):
    """Supported FTP download backends."""

    FTPLIB = "ftplib"


class BrokerConfig(BaseModel):
    """External raw fetch capability declaration loaded from `data.brokers`."""

    model_config = ConfigDict(extra="forbid")

    normalize_profile: str | None = None
    remote_root: str | None = None
    ftp_backend: DownloadBackend | None = None


class SourceConfig(BaseModel):
    """Source execution unit or broker source expansion directive."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    broker: str
    group: str
    raw_object: str | None = None
    outputs: list[str] | None = None
    use_broker_sources: bool = False


class FeatureSetConfig(BaseModel):
    """Feature set declaration loaded from `data.feature_sets`.

    Example:
        config = FeatureSetConfig(enabled=True, version="v1")
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    version: str


class LabelSetConfig(BaseModel):
    """Label set declaration loaded from `data.label_sets`.

    Example:
        config = LabelSetConfig(enabled=True, version="v1")
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    version: str


class DataConfig(BaseModel):
    """Validate broker, source, feature, and label declarations.

    Example:
        config = DataConfig()
    """

    model_config = ConfigDict(extra="forbid")

    # Static broker capability declarations; declared brokers are usable by default.
    brokers: dict[str, BrokerConfig] = Field(default_factory=dict)

    # Static source declarations; entries may be ordinary sources or expansion directives.
    sources: dict[str, SourceConfig] = Field(default_factory=dict)

    # Static feature/label declarations; each set is inactive until enabled.
    feature_sets: dict[str, FeatureSetConfig] = Field(default_factory=dict)
    label_sets: dict[str, LabelSetConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_contract(self) -> Self:
        for broker_name, broker in self.brokers.items():
            if broker_name == "level2_ftp":
                if not broker.remote_root:
                    raise ValueError("data.brokers.level2_ftp.remote_root is required")
                if broker.ftp_backend is None:
                    raise ValueError("data.brokers.level2_ftp.ftp_backend is required")
                continue
            if broker.remote_root is not None or broker.ftp_backend is not None:
                raise ValueError(
                    f"data.brokers.{broker_name} must not declare FTP fields"
                )

        outputs_by_group: dict[str, set[str]] = {}
        for source_name, source in self.sources.items():
            if source.broker not in self.brokers:
                raise ValueError(
                    f"data.sources.{source_name}.broker is not declared: "
                    f"{source.broker}"
                )
            if source.use_broker_sources:
                if source.raw_object is not None or source.outputs is not None:
                    raise ValueError(
                        f"data.sources.{source_name} broker expansion must not "
                        "declare raw_object or outputs"
                    )
                continue

            if not source.raw_object:
                raise ValueError(
                    f"data.sources.{source_name}.raw_object is required"
                )
            if source.outputs is None:
                raise ValueError(
                    f"data.sources.{source_name}.outputs must be explicit"
                )
            if any(not output for output in source.outputs):
                raise ValueError(
                    f"data.sources.{source_name}.outputs must be non-empty names"
                )
            if len(source.outputs) != len(set(source.outputs)):
                raise ValueError(
                    f"data.sources.{source_name}.outputs contains duplicates"
                )
            if not source.enabled:
                continue
            broker = self.brokers[source.broker]
            if source.outputs and not broker.normalize_profile:
                raise ValueError(
                    f"data.brokers.{source.broker}.normalize_profile is required "
                    f"by source {source_name}"
                )
            group_outputs = outputs_by_group.setdefault(source.group, set())
            overlap = group_outputs.intersection(source.outputs)
            if overlap:
                raise ValueError(
                    f"enabled sources in group {source.group!r} produce duplicate "
                    f"outputs: {sorted(overlap)}"
                )
            group_outputs.update(source.outputs)
        return self
