# filepath: src/config/data_config.py
"""Pydantic models for `docs/data/source_contract.md` data configuration."""

from __future__ import annotations

from pathlib import PurePosixPath
from string import Formatter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BrokerConfig(BaseModel):
    """External raw fetch capability declaration loaded from `data.brokers`.

    Example:
        config = BrokerConfig(
            baidupcs_go="BaiduPCS-Go", raw_cache_days=5,
            remote_path_templates=("/level2/{date}/{file}",),
        )
    """

    model_config = ConfigDict(extra="forbid")

    baidupcs_go: str | None = Field(default=None, min_length=1)
    remote_path_templates: tuple[str, ...] | None = None
    raw_cache_days: int | None = Field(default=None, ge=1, strict=True)


class SourceConfig(BaseModel):
    """Describe one complete source execution unit.

    Example:
        config = SourceConfig(
            enabled=True,
            broker="level2_ftp",
            group="offline_level2",
            raw_object="SZ_Trade",
            outputs=["sz_trade"],
        )
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    broker: str
    group: str
    raw_object: str
    outputs: list[str]


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

    # Explicit file-backed source declarations; Tushare sources are code-owned.
    sources: dict[str, SourceConfig] = Field(default_factory=dict)

    # Static feature/label declarations; each set is inactive until enabled.
    feature_sets: dict[str, FeatureSetConfig] = Field(default_factory=dict)
    label_sets: dict[str, LabelSetConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_contract(self) -> Self:
        for broker_name, broker in self.brokers.items():
            if broker_name == "level2_ftp":
                if not broker.baidupcs_go or not broker.baidupcs_go.strip():
                    raise ValueError("data.brokers.level2_ftp.baidupcs_go is required")
                if broker.raw_cache_days is None:
                    raise ValueError("data.brokers.level2_ftp.raw_cache_days is required")
                if not broker.remote_path_templates:
                    raise ValueError("data.brokers.level2_ftp.remote_path_templates is required")
                for template in broker.remote_path_templates:
                    fields = set()
                    for _, name, spec, conversion in Formatter().parse(template):
                        if name is not None:
                            if name not in {"month", "date", "file"} or spec or conversion:
                                raise ValueError("invalid Level-2 remote path placeholder")
                            fields.add(name)
                    if not {"date", "file"}.issubset(fields):
                        raise ValueError("Level-2 remote path requires {date} and {file}")
                    expanded = template.format(
                        month="2026-09", date="2026-09-21", file="SZ_Trade.csv.7z",
                    )
                    path = PurePosixPath(expanded)
                    if (
                        not path.is_absolute() or expanded.startswith("//")
                        or ".." in path.parts or str(path) != expanded
                        or path.name != "SZ_Trade.csv.7z"
                        or any(c in expanded for c in ("\\", "\x00", "\r", "\n"))
                    ):
                        raise ValueError("Level-2 remote path must be a canonical absolute file path")
                continue
            if any(value is not None for value in broker.model_dump().values()):
                raise ValueError(
                    f"data.brokers.{broker_name} must not declare Baidu fields"
                )

        outputs_by_group: dict[str, set[str]] = {}
        for source_name, source in self.sources.items():
            if source.broker not in self.brokers:
                raise ValueError(
                    f"data.sources.{source_name}.broker is not declared: "
                    f"{source.broker}"
                )
            if not source.raw_object:
                raise ValueError(
                    f"data.sources.{source_name}.raw_object must be non-empty"
                )
            if source.broker != "level2_ftp":
                raise ValueError(
                    f"data.sources.{source_name}.broker must be 'level2_ftp'; "
                    "Tushare source selection is code-owned"
                )
            if source.group != "offline_level2":
                raise ValueError(
                    f"data.sources.{source_name}.group must be 'offline_level2'"
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
            group_outputs = outputs_by_group.setdefault(source.group, set())
            overlap = group_outputs.intersection(source.outputs)
            if overlap:
                raise ValueError(
                    f"enabled sources in group {source.group!r} produce duplicate "
                    f"outputs: {sorted(overlap)}"
                )
            group_outputs.update(source.outputs)
        return self
