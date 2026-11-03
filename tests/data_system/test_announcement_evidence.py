# filepath: tests/data_system/test_announcement_evidence.py
"""Preserve original inputs and reject unsupported cross-code identity claims."""

from pathlib import Path

import pytest

from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.context import DataContext
from src.utils.path import PathManager
from tests.fixtures.announcement_evidence import evidence


def test_collection_retains_original_identity_without_requiring_content_fingerprints(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, _, pdf = evidence
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    collection = AnnouncementCollection.model_validate_json(path.read_bytes())
    document, = collection.documents
    assert document.announcement_id == pdf.stem
    assert document.sha256 is None
    assert collection.index_counts[document.symbol] == 1
    with pytest.raises(ValueError, match="unique PDF identities"):
        AnnouncementCollection.model_validate({**collection.model_dump(), "documents": (document, document)})
    with pytest.raises(ValueError, match="request evidence identity"):
        AnnouncementCollection.model_validate({**collection.model_dump(), "request_files": ("../outside.json",)})


def test_alias_must_bind_an_indexed_candidate_and_preserved_primary_evidence(
    evidence: tuple[PathManager, DataContext, Path, Path],
) -> None:
    pm, context, _, _ = evidence
    path = pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
        trade_date=context.start, payload_file="announcements.json")
    payload = AnnouncementCollection.model_validate_json(path.read_bytes()).model_dump()
    symbol = payload["documents"][0]["symbol"]
    payload["security_identities"] = [{"symbol": symbol, "published_symbol": "430685",
        "evidence_url": "https://www.bse.cn/service/code_mapping.html", "evidence_file": "security-code-mapping.html"}]
    with pytest.raises(ValueError, match="original evidence"):
        AnnouncementCollection.model_validate(payload)
    payload["request_files"] = ["security-code-mapping.html"]
    collection = AnnouncementCollection.model_validate(payload)
    assert collection.security_identities[0].symbol == symbol
    payload["security_identities"][0]["symbol"] = "999999"
    with pytest.raises(ValueError, match="company coverage"):
        AnnouncementCollection.model_validate(payload)
