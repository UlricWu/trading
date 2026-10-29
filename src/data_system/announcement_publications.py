# filepath: src/data_system/announcement_publications.py
"""Publication-date facts used to discover new and revised corporate-action evidence."""

from datetime import date, time, timedelta
from pathlib import PurePosixPath
import re
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator
from src.utils.datetime_utils import DateTimeUtils


def is_distribution_evidence(title: str) -> bool:
    """Include implementation plans and potential revisions, excluding explicit unrelated price changes.

    Example:
        assert is_distribution_evidence("关于相关公告更正的说明")
        assert not is_distribution_evidence("关于调整可转债转股价格的公告")
    """
    title = re.sub(r"[（(]已取消[）)]\s*$", "", title)
    return bool(re.search(r"分派|分配|分红|派息|转增|转赠|除权|除息|重整|业绩.*补偿|更正|补充|修订|取消|终止", title)
        and not re.search(r"(?:回购(?:股份)?|转股|发行|授予|行权)价(?:格)?|律师|独立财务|激励计划|募集资金|分红回报规划", title))


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

    @property
    def available_ts_us(self) -> int:
        """Use a verified publication instant or the next natural day's start.

        Example:
            visible = publication.available_ts_us <= decision_ts_us
        """
        return self.published_ts_us if self.published_ts_us is not None else (
            DateTimeUtils.local_time_to_utc_epoch_us(time(), self.published_date + timedelta(days=1)))

    @model_validator(mode="after")
    def _validate_publication_time(self) -> Self:
        if self.published_ts_us is not None and DateTimeUtils.to_local(
            DateTimeUtils.from_utc_epoch_us(self.published_ts_us)
        ).date() != self.published_date:
            raise ValueError("exact publication time must match the local publication date")
        return self


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
    """Record index coverage and the observed public response locations.

    Example:
        coverage = PublicationCoverage.model_validate_json(path.read_bytes())
        print(coverage.index_count, coverage.observed_at)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    index_count: int = Field(ge=0)
    observed_at: AwareDatetime
    request_files: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_evidence_paths(self) -> Self:
        if any(PurePosixPath(name).name != name for name in self.request_files):
            raise ValueError("invalid publication query evidence identity")
        return self
