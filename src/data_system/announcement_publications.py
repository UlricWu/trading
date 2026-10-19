# filepath: src/data_system/announcement_publications.py
"""Publication-date facts used to discover new and revised corporate-action evidence."""

from datetime import date
from pathlib import PurePosixPath
import re
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator


def is_distribution_evidence(title: str) -> bool:
    """Include implementation plans and potential revisions, excluding explicit unrelated price changes.

    Example:
        assert is_distribution_evidence("关于相关公告更正的说明")
        assert not is_distribution_evidence("关于调整可转债转股价格的公告")
    """
    return bool(re.search(r"分派|分配|分红|派息|转增|除权|除息|更正|补充|修订|取消|终止", title)
        and not re.search(r"(?:回购(?:股份)?|转股|发行|授予|行权)价(?:格)?|律师|独立财务|激励计划|募集资金", title))


class AnnouncementPublication(BaseModel):
    """A source-native index identity before any PDF terms are approved.

    Example:
        publication = AnnouncementPublication(source="cninfo", announcement_id="1225572338",
            symbol="002986", title="权益分派更正公告", published_date="2026-09-19",
            url="https://static.cninfo.com.cn/finalpage/2026-09-19/1225572338.PDF")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["cninfo", "bse"]
    announcement_id: str = Field(pattern=r"^(?:[a-f0-9]+|manual[0-9]+)$")
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    title: str
    published_date: date
    published_ts_us: StrictInt | None = None
    url: str


class PublicationIndex(BaseModel):
    """A complete source index for one natural publication day, independent of dividends.

    Example:
        index = PublicationIndex.model_validate_json(payload.read_bytes())
        assert all(row.published_date == index.publication_date for row in index.announcements)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["v1"]
    source: Literal["cninfo", "bse"]
    publication_date: date
    announcements: tuple[AnnouncementPublication, ...]

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        identities = {row.announcement_id for row in self.announcements}
        if len(identities) != len(self.announcements):
            raise ValueError("publication index contains duplicate source identities")
        if any(row.source != self.source or row.published_date != self.publication_date for row in self.announcements):
            raise ValueError("publication index contains rows outside its source or day")
        return self


class PublicationCoverage(BaseModel):
    """Bind a complete publication index to the exact observed public responses.

    Example:
        coverage = PublicationCoverage.model_validate_json(path.read_bytes())
        assert coverage.index_sha256 == sha256(index_path.read_bytes()).hexdigest()
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    index_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    index_count: int = Field(ge=0)
    observed_at: AwareDatetime
    request_sha256: dict[str, str]

    @model_validator(mode="after")
    def _validate_evidence_paths(self) -> Self:
        if any(PurePosixPath(name).name != name or not re.fullmatch(r"[a-f0-9]{64}", digest)
               for name, digest in self.request_sha256.items()):
            raise ValueError("invalid publication query evidence identity")
        return self


class CorporateActionMaintenance(BaseModel):
    """Report publication discovery separately from the resulting candidate dispositions.

    Unmatched publications are review work, not automatically confirmed distributions.

    Example:
        result = run_corporate_action_maintenance(path_manager=pm, publication_interval=interval)
        print(result.affected_registration_dates, result.resolution_counts)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["v1"]
    publication_start: date
    publication_end: date
    affected_registration_dates: tuple[str, ...]
    unmatched_publications: tuple[AnnouncementPublication, ...]
    resolution_counts: dict[str, int]
    input_sha256: dict[str, str]
    output_sha256: dict[str, str]
