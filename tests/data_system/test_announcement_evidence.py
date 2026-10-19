# filepath: tests/data_system/test_announcement_evidence.py
"""Preserve original inputs and reject unsupported cross-code identity claims."""

from hashlib import sha256
from pathlib import Path

import pytest

from src.data_system.announcement_evidence import AnnouncementCollection, preserve_announcement_collection
from src.data_system.context import DataContext
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager
from tests.fixtures.announcement_evidence import evidence


def test_replacement_keeps_the_complete_prior_input_snapshot(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, _, pdf = evidence
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    payload = path.read_bytes()
    collection = AnnouncementCollection.model_validate_json(payload)
    archive = preserve_announcement_collection(path=path, collection=collection)
    assert archive.name == sha256(payload).hexdigest()
    assert (archive / "announcements.json").read_bytes() == payload
    original = pdf.read_bytes()
    assert (archive / pdf.name).read_bytes() == original
    assert preserve_announcement_collection(path=path, collection=collection) == archive
    FileSystem.write_bytes_atomic(pdf, b"%PDF-changed")
    assert (archive / pdf.name).read_bytes() == original
    with pytest.raises(ValueError, match="cannot archive changed"):
        preserve_announcement_collection(path=path, collection=collection)


@pytest.mark.parametrize("filename", ["announcements.json", "1.pdf"])
def test_existing_archive_rejects_same_size_content_changes(
    evidence: tuple[PathManager, DataContext, Path, Path], filename: str,
) -> None:
    pm, context, _, _ = evidence
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    archive = preserve_announcement_collection(path=path, collection=collection)
    original = (archive / filename).read_bytes()
    changed = bytes([original[0] ^ 1]) + original[1:]
    FileSystem.write_bytes_atomic(archive / filename, changed)
    with pytest.raises(ValueError, match="archived announcement evidence changed"):
        preserve_announcement_collection(path=path, collection=collection)
    assert (archive / filename).read_bytes() == changed
    assert (path.parent / filename).read_bytes() == original


def test_alias_must_bind_an_indexed_candidate_and_preserved_primary_evidence(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, _, _ = evidence
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    payload = AnnouncementCollection.model_validate_json(path.read_bytes()).model_dump()
    symbol = payload["documents"][0]["symbol"]
    payload["security_identities"] = [{"symbol": symbol, "published_symbol": "430685",
        "evidence_url": "https://www.bse.cn/service/code_mapping.html", "evidence_sha256": "2" * 64}]
    with pytest.raises(ValueError, match="original evidence"):
        AnnouncementCollection.model_validate(payload)
    payload["request_sha256"] = {"security-code-mapping.html": "2" * 64}
    collection = AnnouncementCollection.model_validate(payload)
    assert collection.security_identities[0].symbol == symbol
    payload["security_identities"][0]["symbol"] = "999999"
    with pytest.raises(ValueError, match="company coverage"):
        AnnouncementCollection.model_validate(payload)
