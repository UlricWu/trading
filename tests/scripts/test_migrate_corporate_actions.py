# filepath: tests/scripts/test_migrate_corporate_actions.py
"""Isolated V1 migration preserves confirmed business facts and original evidence."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pyarrow as pa
import pytest

from scripts.migrate_corporate_actions import main, migrate_corporate_actions
from src.access import Access, meta
from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.announcement_publications import PublicationCoverage
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord
from src.data_system.distribution_announcements import ReviewExtractionManifest
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


def _publish_json(pm: PathManager, path: Path, payload: object) -> None:
    FileSystem.write_bytes_atomic(path, json.dumps(payload).encode())
    meta.commit(pm=pm, payload_path=path)


@pytest.fixture
def source(tmp_path: Path) -> PathManager:
    root = tmp_path / "source"
    root.mkdir()
    pm = PathManager(root)
    cases = json.loads((Path(__file__).parents[1] / "fixtures/corporate_actions/verified_distributions.json").read_bytes())
    for case in cases:
        review = case["review"]
        day = review["record_date"]
        raw = pm.raw_payload(broker="tushare", source_name="dividend", trade_date=day, payload_file="data.parquet")
        records = [{key: value for key, value in row.items() if key not in ("source_row_id", "symbol")}
                   for row in case["records"]]
        write_parquet_atomic(output_file=raw, table=pa.Table.from_pylist(records))
        meta.commit(pm=pm, payload_path=raw)
        for source_name in ("corporate_action_review_inputs", "corporate_action_reviews"):
            path = pm.raw_payload(broker="announcements", source_name=source_name, trade_date=day, payload_file="reviews.json")
            _publish_json(pm, path, [review])
        manifest_path = path.parent / "extraction.json"
        manifest_path.write_text(json.dumps({"inputs": {str(raw.relative_to(root)): "0" * 64},
            "producer_sha256": "1" * 64, "reviews_sha256": "2" * 64, "issues": {}}))
        prior = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=day)
        candidates = (review["candidate_id"], *review.get("related_sources", {}))
        write_parquet_atomic(output_file=prior.payload_path, table=pa.Table.from_pylist([
            {"symbol": case["records"][0]["symbol"], "record_date": day,
             "resolution": json.dumps({"candidate_id": candidate, "review": review,
                "status": "not_entitled" if not any(float(t["cash_per_share"]) or float(t["shares_per_share"])
                    for t in review["terms"]) else "resolved"})} for candidate in candidates]))
        meta.commit(pm=pm, payload_path=prior.payload_path)
    return pm


def test_migration_preserves_all_versions_associations_and_source_files(source: PathManager, tmp_path: Path) -> None:
    before = {path.relative_to(source.storage_root): path.read_bytes()
              for path in source.storage_root.rglob("*") if path.is_file()}
    output = tmp_path / "converted"
    assert main(["--source-root", str(source.storage_root), "--output-dir", str(output)]) == 0
    report = json.loads((output / "report.json").read_bytes())
    assert report["partitions"] == 6
    assert report["reviewed_events"] == report["committed_reviews"] == report["manual_reviews"] == 6
    assert report["reviewed_candidates"] == report["candidate_count"] == 7
    assert report["statuses"] == {"resolved": 6, "not_entitled": 1}
    assert json.loads((output / "unresolved.json").read_bytes()) == []
    pm = PathManager(output / "storage")
    access = Access(pm=pm, processed_version="v1")
    for path in pm.raw_source_dir(broker="announcements", source_name="corporate_action_reviews").glob("trade_date=*/reviews.json"):
        relative = path.relative_to(pm.storage_root)
        old, = json.loads(before[relative])
        new, = json.loads(path.read_bytes())
        assert new["terms"] == [{"published_ts_us": None, "reference_price": None,
            "benefit_state": "confirmed", "pay_date": None, "shares_credit_date": None,
            "shares_available_date": None, "shares_numerator": None, "shares_denominator": None,
            **terms} for terms in old["terms"]]
        for key in ("candidate_id", "action_id", "record_date", "ex_date", "pay_date", "div_listdate", "shareholder_scope", "reason"):
            assert new[key] == old[key]
        assert new["related_sources"] == list(old.get("related_sources", {}))
        assert new["evidence_refs"] == [ref.split("#sha256=", 1)[0] for ref in old["evidence_refs"]]
        review = CorporateActionReview.model_validate(new)
        assert all(item.review == review for item in access.corporate_actions(record_date=old["record_date"]))
        manifest = ReviewExtractionManifest.model_validate_json((path.parent / "extraction.json").read_bytes())
        assert manifest.issues == {}
        assert len(manifest.inputs) == 1
    assert {p.relative_to(source.storage_root): p.read_bytes() for p in source.storage_root.rglob("*") if p.is_file()} == before
    assert all((output / "original" / relative).read_bytes() == payload for relative, payload in before.items())


def test_migration_merges_equivalent_observations_and_preserves_real_conflicts(source: PathManager, tmp_path: Path) -> None:
    row = json.loads((Path(__file__).parents[1] / "fixtures/corporate_actions/ordinary_dividend.json").read_bytes())
    raw_row = {key: value for key, value in row.items() if key != "symbol"}
    duplicate = {**raw_row, "ts_code": "000001.SZ"}
    conflicting = {**raw_row, "ts_code": "000002.SZ"}
    day = row["record_date"]
    raw = source.raw_payload(broker="tushare", source_name="dividend", trade_date=day, payload_file="data.parquet")
    write_parquet_atomic(output_file=raw, table=pa.Table.from_pylist([
        raw_row, duplicate, duplicate, conflicting, {**conflicting, "cash_div_tax": "2"}]))
    meta.commit(pm=source, payload_path=raw)
    path = source.raw_payload(broker="announcements", source_name="corporate_action_reviews", trade_date=day, payload_file="reviews.json")
    _publish_json(source, path, [])
    candidate = f"{row['ts_code']}/{row['end_date']}/{day}"
    (path.parent / "extraction.json").write_text(json.dumps({"inputs": {}, "producer_sha256": "1" * 64,
        "reviews_sha256": "2" * 64, "issues": {candidate: "unsupported_announcement_exchange"}}))
    output = tmp_path / "converted"
    report_path = migrate_corporate_actions(source_root=source.storage_root, output_dir=output)
    report = json.loads(report_path.read_bytes())
    assert report["source_candidates"] == 2
    assert report["unresolved_reasons"] == {"different_distribution_terms": 1}
    values = Access(pm=PathManager(output / "storage"), processed_version="v1").corporate_actions(record_date=day)
    assert [item.status for item in values] == ["resolved", "unresolved", "resolved"]
    assert values[0].source_record.cash_div_tax == Decimal("1")
    assert values[0].source_record.stk_div == Decimal("0.5")
    assert values[1].source_record is None
    assert values[2].source_record.model_dump(mode="json") == {**row, "base_date": None, "base_share": None}
    assert (output / "original" / raw.relative_to(source.storage_root)).read_bytes() == raw.read_bytes()


@pytest.mark.parametrize("failure", ["missing_raw", "lost_review", "conflicting_manual"])
def test_migration_refuses_loss_of_existing_facts(source: PathManager, tmp_path: Path, failure: str) -> None:
    day = "2024-07-23"
    path = source.raw_payload(broker="announcements", source_name="corporate_action_reviews", trade_date=day, payload_file="reviews.json")
    if failure == "missing_raw":
        source.raw_meta(broker="tushare", source_name="dividend", trade_date=day).unlink()
    elif failure == "lost_review":
        _publish_json(source, path, [])
        manual = source.raw_payload(broker="announcements", source_name="corporate_action_review_inputs", trade_date=day, payload_file="reviews.json")
        _publish_json(source, manual, [])
    else:
        manual = source.raw_payload(broker="announcements", source_name="corporate_action_review_inputs", trade_date=day, payload_file="reviews.json")
        reviews = json.loads(manual.read_bytes())
        reviews[0]["terms"][0]["cash_per_share"] = "1.5"
        _publish_json(source, manual, reviews)
    with pytest.raises((FileNotFoundError, ValueError), match="Meta|lost or changed|manual review"):
        migrate_corporate_actions(source_root=source.storage_root, output_dir=tmp_path / "converted")
    assert not (tmp_path / "converted/report.json").exists()


def test_migration_converts_collection_identities_without_reading_pdf_contents(source: PathManager, tmp_path: Path) -> None:
    day = "2024-07-23"
    path = source.raw_payload(broker="bse", source_name="corporate_action_announcements", trade_date=day, payload_file="announcements.json")
    digest = "a" * 64
    fields = {"version": "v1", "dividend_sha256": "b" * 64, "announcement_start": "2024-07-01",
        "announcement_end": day, "collected_at": "2026-10-17T00:00:00Z", "documents": [],
        "index_counts": {"920685": 1}, "request_sha256": {f"{digest}.html": digest},
        "security_identities": [{"symbol": "920685", "published_symbol": "430685",
            "evidence_url": "https://www.bse.cn/mapping", "evidence_sha256": digest}]}
    _publish_json(source, path, fields)
    original_evidence = path.parent / f"{digest}.html"
    original_evidence.write_bytes(b"original identity evidence")
    original_manifest = deepcopy(fields)
    output = tmp_path / "converted"
    migrate_corporate_actions(source_root=source.storage_root, output_dir=output)
    migrated = output / "storage" / path.relative_to(source.storage_root)
    collection = AnnouncementCollection.model_validate_json(migrated.read_bytes())
    assert collection.security_identities[0].evidence_file == f"{digest}.html"
    assert collection.request_files == (f"{digest}.html",)
    assert (migrated.parent / collection.security_identities[0].evidence_file).read_bytes() == original_evidence.read_bytes()
    assert json.loads(path.read_bytes()) == original_manifest


def test_migration_refuses_in_place_and_existing_output(source: PathManager, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="disjoint"):
        migrate_corporate_actions(source_root=source.storage_root, output_dir=source.storage_root / "new")
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(FileExistsError, match="must not exist"):
        migrate_corporate_actions(source_root=source.storage_root, output_dir=output)
    assert marker.read_text() == "keep"


def test_migration_preserves_publication_coverage_and_failed_refresh_marker(source: PathManager, tmp_path: Path) -> None:
    path = source.raw_payload(broker="cninfo", source_name="corporate_action_publications",
        trade_date="2024-07-18", payload_file="index.json")
    _publish_json(source, path, {"version": "v1", "source": "cninfo", "publication_date": "2024-07-18",
        "announcements": []})
    (path.parent / "coverage.json").write_text(json.dumps({"index_sha256": "a" * 64, "index_count": 0,
        "observed_at": "2026-10-17T00:00:00Z", "request_sha256": {"query.json": "b" * 64}}))
    (path.parent / "query.json").write_text("[]")
    (path.parent / "pending.json").write_text('{"refresh": "incomplete"}')
    output = tmp_path / "converted"
    migrate_corporate_actions(source_root=source.storage_root, output_dir=output)
    migrated = output / "storage" / path.relative_to(source.storage_root)
    coverage = PublicationCoverage.model_validate_json((migrated.parent / "coverage.json").read_bytes())
    assert coverage.index_count == 0
    assert coverage.request_files == ("query.json",)
    assert (migrated.parent / "pending.json").read_bytes() == (path.parent / "pending.json").read_bytes()


@pytest.mark.parametrize("amount_issue", ["distribution_amount_ambiguous", "distribution_amount_conflict"])
def test_migration_keeps_confirmed_supplements_and_distinguishes_gaps_from_conflicts(
    tmp_path: Path, amount_issue: str,
) -> None:
    cases = json.loads((Path(__file__).parents[1] / "fixtures/corporate_actions/confirmed_supplements.json").read_bytes())
    source_root = tmp_path / "source"
    source_root.mkdir()
    pm = PathManager(source_root)
    schema = pa.schema([(key, pa.string()) for key in cases[0]["records"][0] if key != "symbol"])
    for case in cases:
        if case["issue"] == "distribution_amount_ambiguous":
            case["issue"] = amount_issue
        day = case["record_date"]
        raw = pm.raw_payload(broker="tushare", source_name="dividend", trade_date=day, payload_file="data.parquet")
        records = [{key: value for key, value in row.items() if key != "symbol"} for row in case["records"]]
        write_parquet_atomic(output_file=raw, table=pa.Table.from_pylist(records, schema=schema))
        meta.commit(pm=pm, payload_path=raw)
        reviews = [case["review"]] if case["review"] is not None else []
        for source_name in ("corporate_action_review_inputs", "corporate_action_reviews"):
            path = pm.raw_payload(broker="announcements", source_name=source_name, trade_date=day, payload_file="reviews.json")
            _publish_json(pm, path, reviews)
        manifest = ReviewExtractionManifest(inputs=(), issues=(
            {case["candidate_id"]: case["issue"]} if case["issue"] is not None else {}))
        (path.parent / "extraction.json").write_text(manifest.model_dump_json())

    output = tmp_path / "converted"
    migrate_corporate_actions(source_root=pm.storage_root, output_dir=output)
    access = Access(pm=PathManager(output / "storage"), processed_version="v1")
    for case in cases:
        actual = access.corporate_actions(record_date=case["record_date"])
        if case["review"] is None:
            item, = actual
            assert item.review is None
            if case["issue"] == "distribution_amount_ambiguous":
                assert item.status == "resolved" and item.reason == "ordinary_dividend_source"
                assert item.source_record == DividendRecord.model_validate(case["records"][0])
            else:
                assert item.status == "unresolved" and item.source_record is None
                assert item.reason == case["issue"]
        else:
            expected = CorporateActionReview.model_validate(case["review"])
            assert {item.candidate_id for item in actual} == {expected.candidate_id, *expected.related_sources}
            assert all(item.status == case["status"] and item.review == expected for item in actual)
            if expected.announcement_source is not None:
                assert not case["records"]
                assert actual[0].candidate_id == "cninfo:1223733350"
                assert expected.terms[0].published_date.isoformat() == "2025-06-03"
            if expected.candidate_id.startswith("002349."):
                assert expected.terms[1].published_ts_us == 1625010070000000
                assert expected.terms[0].reference_cash_per_share == Decimal("0.399430")
                assert expected.terms[1].reference_cash_per_share == Decimal("0.039943")
            if expected.candidate_id.startswith("003001."):
                assert expected.terms[0].reference_shares_per_share == Decimal("0.3921344")
                assert expected.terms[0].published_date.isoformat() == "2025-06-30"
                assert expected.terms[1].published_date.isoformat() == "2025-07-05"
