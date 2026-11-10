# filepath: src/data_system/announcement_evidence.py
"""Original disclosure evidence shared by official public announcement sources."""

from datetime import date
from pathlib import Path
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator


class AnnouncementFile(BaseModel):
    """An original PDF; its security code and native source ID remain unchanged.

    Example:
        original = AnnouncementFile.model_validate(manifest_row)
        payload = directory / f"{original.announcement_id}.pdf"
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    announcement_id: str = Field(pattern=r"^(?:[a-f0-9]+|manual[0-9]+)$")
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    title: str
    published_date: date
    published_ts_us: StrictInt | None = None
    url: str
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    fetched_at: AwareDatetime


class SecurityIdentity(BaseModel):
    """A source lookup alias supported by an original official document.

    This identifies an issuer across code changes; it does not establish historical
    listing membership or the time at which a security could be traded.

    Example:
        alias = SecurityIdentity(symbol="920685", published_symbol="430685",
            evidence_url="https://www.bse.cn/service/code_mapping.html", evidence_file="security-code-mapping.html")
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    published_symbol: str = Field(pattern=r"^[0-9]{6}$")
    evidence_url: str
    evidence_file: str = Field(pattern=r"^[^/\\]+$")


class AnnouncementCollection(BaseModel):
    """One source's bounded company indexes and their exact original documents.

    Example:
        collection = AnnouncementCollection.model_validate_json(payload.read_bytes())
        print(len(collection.documents))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(pattern=r"^v1$")
    announcement_start: date
    announcement_end: date
    collected_at: AwareDatetime
    documents: tuple[AnnouncementFile, ...]
    index_counts: dict[str, Annotated[int, Field(gt=0)]]
    request_files: tuple[str, ...]
    security_identities: tuple[SecurityIdentity, ...] = ()
    query_scope: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_collection(self) -> Self:
        if self.announcement_start > self.announcement_end:
            raise ValueError("announcement window must be increasing")
        identities = [document.announcement_id for document in self.documents]
        if len(set(identities)) != len(identities):
            raise ValueError("announcement collection must have unique PDF identities")
        aliases = {row.published_symbol: row.symbol for row in self.security_identities}
        if len(aliases) != len(self.security_identities):
            raise ValueError("historical security code has ambiguous source identity")
        for row in self.security_identities:
            if row.symbol not in self.index_counts or row.evidence_file not in self.request_files:
                raise ValueError("security alias lacks company coverage or original evidence")
        if any(document.symbol not in self.index_counts and aliases.get(document.symbol) not in self.index_counts
               for document in self.documents):
            raise ValueError("announcement PDF lacks company index coverage")
        for name in self.request_files:
            if Path(name).name != name:
                raise ValueError("invalid announcement request evidence identity")
        return self
