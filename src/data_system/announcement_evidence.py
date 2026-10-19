# filepath: src/data_system/announcement_evidence.py
"""Original disclosure evidence shared by official public announcement sources."""

from datetime import date
from hashlib import sha256
from pathlib import Path
import re
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.utils.filesystem import FileSystem


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
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fetched_at: AwareDatetime


class SecurityIdentity(BaseModel):
    """A source lookup alias supported by an original official document.

    This identifies an issuer across code changes; it does not establish historical
    listing membership or the time at which a security could be traded.

    Example:
        alias = SecurityIdentity(symbol="920685", published_symbol="430685",
            evidence_url="https://www.bse.cn/service/code_mapping.html", evidence_sha256="0" * 64)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    published_symbol: str = Field(pattern=r"^[0-9]{6}$")
    evidence_url: str
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class AnnouncementCollection(BaseModel):
    """One source's bounded company indexes and their exact original documents.

    Example:
        collection = AnnouncementCollection.model_validate_json(payload.read_bytes())
        print(len(collection.documents))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(pattern=r"^v1$")
    dividend_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    announcement_start: date
    announcement_end: date
    collected_at: AwareDatetime
    documents: tuple[AnnouncementFile, ...]
    index_counts: dict[str, Annotated[int, Field(gt=0)]]
    request_sha256: dict[str, str]
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
            if row.symbol not in self.index_counts or row.evidence_sha256 not in self.request_sha256.values():
                raise ValueError("security alias lacks company coverage or original evidence")
        if any(document.symbol not in self.index_counts and aliases.get(document.symbol) not in self.index_counts
               for document in self.documents):
            raise ValueError("announcement PDF lacks company index coverage")
        for name, digest in self.request_sha256.items():
            if Path(name).name != name or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("invalid announcement request evidence identity")
        return self


def preserve_announcement_collection(*, path: Path, collection: AnnouncementCollection) -> Path:
    """Keep a complete immutable input snapshot before replacing a collection.

    These archives are evidence, never alternate runtime datasets.

    Example:
        archive = preserve_announcement_collection(path=collection_path, collection=previous)
        assert (archive / "announcements.json").is_file()
    """
    payload = path.read_bytes()
    collection_digest = sha256(payload).hexdigest()
    archive = path.parent / "history" / collection_digest
    expected = {**collection.request_sha256,
        **{f"{row.announcement_id}.pdf": row.sha256 for row in collection.documents},
        path.name: collection_digest}
    for name, digest in expected.items():
        original = payload if name == path.name else (path.parent / name).read_bytes()
        if name != path.name and sha256(original).hexdigest() != digest:
            raise ValueError(f"cannot archive changed announcement evidence: {name}")
        target = archive / name
        if not target.exists():
            FileSystem.write_bytes_atomic(target, original)
        elif target.read_bytes() != original:
            raise ValueError(f"archived announcement evidence changed: {target}")
    for name in ("meta.json", *(f"{row.announcement_id}.download.json" for row in collection.documents)):
        original = path.parent / name
        if original.exists() and not (archive / name).exists():
            FileSystem.write_bytes_atomic(archive / name, original.read_bytes())
    return archive
