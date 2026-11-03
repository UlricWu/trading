# filepath: scripts/migrate_corporate_actions.py
"""Rebuild company-action V1 objects in a new, isolated directory.

Example:
    python -m scripts.migrate_corporate_actions \
        --source-root /data/formal --output-dir /data/validation/company-actions

The output contains unchanged originals, a rebuilt storage root and a JSON report.
The source is only read. Existing output directories are refused, including failed
runs; retain them as evidence and use a new directory for the next attempt.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import shutil

import pyarrow.parquet as pq
from pydantic import TypeAdapter

from src.access import Access, meta
from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.announcement_publications import PublicationCoverage, PublicationIndex
from src.data_system.context import DataContext
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord
from src.data_system.distribution_announcements import ReviewExtractionManifest
from src.data_system.normalize.tushare import normalize_tushare
from src.data_system.steps.resolve_corporate_actions import ResolveCorporateActionsStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


def _convert_review(payload: Mapping[str, object]) -> CorporateActionReview:
    fields = dict(payload)
    fields.pop("source_digest", None)
    related = fields.get("related_sources", ())
    if isinstance(related, dict):
        fields["related_sources"] = list(related)
    refs = TypeAdapter(tuple[str, ...]).validate_python(fields["evidence_refs"])
    fields["evidence_refs"] = tuple(ref.split("#sha256=", 1)[0] for ref in refs)
    return CorporateActionReview.model_validate(fields)


def _read_reviews(path: Path) -> tuple[CorporateActionReview, ...]:
    payload = TypeAdapter(list[dict[str, object]]).validate_json(path.read_bytes())
    return tuple(_convert_review(row) for row in payload)


def migrate_corporate_actions(*, source_root: Path, output_dir: Path) -> Path:
    """Copy existing evidence and rebuild V1 without fetching or rechecking PDFs.

    All source partitions are included. Conflicting confirmed reviews or a lost
    source partition fail the run instead of silently discarding prior decisions.

    Example:
        report = migrate_corporate_actions(source_root=Path("/data/formal"),
            output_dir=Path("/data/validation/company-actions"))
        print(report.read_text())
    """
    if not source_root.is_absolute() or not output_dir.is_absolute():
        raise ValueError("source_root and output_dir must be absolute paths")
    source_root = source_root.resolve(strict=True)
    output_dir = output_dir.resolve()
    if output_dir.is_relative_to(source_root) or source_root.is_relative_to(output_dir):
        raise ValueError("source_root and output_dir must be disjoint")
    if output_dir.exists():
        raise FileExistsError(f"output_dir must not exist: {output_dir}")
    if not tuple((source_root / "raw/tushare/dividend").glob("trade_date=*/meta.json")):
        raise FileNotFoundError("source_root has no committed raw dividend partitions")

    raw_scopes = ("raw/tushare/dividend", "raw/announcements/corporate_action_review_inputs",
        "raw/announcements/corporate_action_reviews", "raw/cninfo/corporate_action_announcements",
        "raw/bse/corporate_action_announcements", "raw/cninfo/corporate_action_publications",
        "raw/bse/corporate_action_publications")
    output_dir.mkdir(parents=True)
    original_root = output_dir / "original"
    storage_root = output_dir / "storage"
    original_root.mkdir()
    storage_root.mkdir()
    inventory = []
    for relative in (*raw_scopes, "processed/dividend/v1", "processed/corporate_actions/v1"):
        source = source_root / relative
        if source.exists():
            shutil.copytree(source, original_root / relative)
    original = PathManager(original_root)
    for path in sorted(original_root.rglob("*")):
        if path.is_file():
            inventory.append({"path": path.relative_to(original_root).as_posix(), "size_bytes": path.stat().st_size})
    FileSystem.write_bytes_atomic(output_dir / "inputs.json", json.dumps(inventory, indent=2).encode())
    for relative in raw_scopes:
        if (original_root / relative).exists():
            shutil.copytree(original_root / relative, storage_root / relative)
    pm = PathManager(storage_root)
    raw_dir = pm.raw_source_dir(broker="tushare", source_name="dividend")
    days = set(path.name.removeprefix("trade_date=") for path in raw_dir.glob("trade_date=*"))
    for relative in (*raw_scopes[1:3], *raw_scopes[3:5], "processed/dividend/v1", "processed/corporate_actions/v1"):
        days.update(path.name.removeprefix("trade_date=")
            for path in (original_root / relative).glob("trade_date=*"))
    ordered_days = tuple(sorted(DateTimeUtils.require_system_date(day, field_name="registration date") for day in days))
    source_rows = implemented_rows = committed_reviews = manual_reviews = 0
    for day in ordered_days:
        raw_meta = pm.raw_meta(broker="tushare", source_name="dividend", trade_date=day)
        raw = meta.require(pm=pm, meta_path=raw_meta)
        paths = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
        table = normalize_tushare(input_file=raw.payload_path, output_name=paths.payload_path,
            raw_object="dividend", target_name="dividend", trade_date=day).table
        records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(table.to_pylist())
        source_rows += len(records)
        implemented_rows += sum(row.div_proc == "实施" for row in records)
        write_parquet_atomic(output_file=paths.payload_path, table=table)
        meta.commit(pm=pm, payload_path=paths.payload_path, upstream_meta_path=raw_meta)

        reviews_by_source: dict[str, tuple[CorporateActionReview, ...]] = {}
        for source_name in ("corporate_action_review_inputs", "corporate_action_reviews"):
            old_path = original.raw_payload(broker="announcements", source_name=source_name,
                trade_date=day, payload_file="reviews.json")
            old = meta.find(pm=original, meta_path=old_path.parent / "meta.json", expected_payload_path=old_path)
            if old is None:
                if old_path.exists():
                    raise ValueError(f"review payload lacks committed Meta: {old_path}")
                reviews_by_source[source_name] = ()
                continue
            reviews = _read_reviews(old_path)
            if any(review.record_date.isoformat() != day for review in reviews):
                raise ValueError(f"review registration does not match partition: {day}")
            reviews_by_source[source_name] = reviews
            converted = pm.raw_payload(broker="announcements", source_name=source_name,
                trade_date=day, payload_file="reviews.json")
            FileSystem.write_bytes_atomic(converted, TypeAdapter(tuple[CorporateActionReview, ...]).dump_json(reviews))
            meta.commit(pm=pm, payload_path=converted,
                upstream_meta_path=paths.meta_path if source_name == "corporate_action_reviews" else None)
            extraction_path = converted.parent / "extraction.json"
            if extraction_path.exists():
                fields = json.loads(extraction_path.read_bytes())
                fields.pop("producer_sha256", None)
                fields.pop("reviews_sha256", None)
                if isinstance(fields["inputs"], dict):
                    fields["inputs"] = list(fields["inputs"])
                manifest = ReviewExtractionManifest.model_validate(fields)
                FileSystem.write_bytes_atomic(extraction_path, manifest.model_dump_json(indent=2).encode())
        published = {review.candidate_id: review for review in reviews_by_source["corporate_action_reviews"]}
        for review in reviews_by_source["corporate_action_review_inputs"]:
            if published.get(review.candidate_id) != review:
                raise ValueError(f"manual review is absent or conflicts with committed review: {review.candidate_id}")
        committed_reviews += len(reviews_by_source["corporate_action_reviews"])
        manual_reviews += len(reviews_by_source["corporate_action_review_inputs"])

        for provider in ("cninfo", "bse"):
            collection_path = pm.raw_payload(broker=provider, source_name="corporate_action_announcements",
                trade_date=day, payload_file="announcements.json")
            if not collection_path.exists():
                continue
            original_collection = original_root / collection_path.relative_to(storage_root)
            meta.require(pm=original, meta_path=original_collection.parent / "meta.json",
                expected_payload_path=original_collection)
            fields = json.loads(collection_path.read_bytes())
            fields.pop("dividend_sha256", None)
            requests = fields.pop("request_sha256", None)
            if requests is not None:
                fields["request_files"] = list(requests)
            for identity in fields.get("security_identities", []):
                digest = identity.pop("evidence_sha256", None)
                if digest is not None:
                    if requests is None:
                        raise ValueError(f"legacy security identity lacks its request filenames: {collection_path}")
                    filenames = [name for name, value in requests.items() if value == digest]
                    if len(filenames) != 1:
                        raise ValueError(f"security identity needs one original evidence file: {collection_path}")
                    identity["evidence_file"] = filenames[0]
            for document in fields["documents"]:
                document.pop("sha256", None)
            collection = AnnouncementCollection.model_validate(fields)
            for name in (*collection.request_files, *(f"{row.announcement_id}.pdf" for row in collection.documents)):
                if not (collection_path.parent / name).is_file():
                    raise FileNotFoundError(collection_path.parent / name)
            FileSystem.write_bytes_atomic(collection_path, collection.model_dump_json(indent=2).encode())
            meta.commit(pm=pm, payload_path=collection_path, upstream_meta_path=paths.meta_path)

    for provider in ("cninfo", "bse"):
        root = pm.raw_source_dir(broker=provider, source_name="corporate_action_publications")
        for path in sorted(root.glob("trade_date=*/coverage.json")):
            fields = json.loads(path.read_bytes())
            fields.pop("index_sha256", None)
            if "request_sha256" in fields:
                fields["request_files"] = list(fields.pop("request_sha256"))
            coverage = PublicationCoverage.model_validate(fields)
            index_path = path.parent / "index.json"
            meta.require(pm=pm, meta_path=path.parent / "meta.json", expected_payload_path=index_path)
            index = PublicationIndex.model_validate_json(index_path.read_bytes())
            if coverage.index_count != len(index.announcements):
                raise ValueError(f"publication coverage count differs from index: {path}")
            FileSystem.write_bytes_atomic(path, coverage.model_dump_json(indent=2).encode())

    context = DataContext(start=ordered_days[0], end=ordered_days[-1], trade_dates=ordered_days)
    ResolveCorporateActionsStep(pm).run(context)
    access = Access(pm=pm, processed_version="v1")
    statuses: Counter[str] = Counter()
    old_statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    origins: Counter[str] = Counter()
    reviewed_events: set[str] = set()
    unresolved = []
    for day in ordered_days:
        resolutions = access.corporate_actions(record_date=day)
        by_candidate = {item.candidate_id: item for item in resolutions}
        old_paths = original.processed_object(dataset_name="corporate_actions", version="v1", trade_date=day)
        old = meta.find(pm=original, meta_path=old_paths.meta_path, expected_payload_path=old_paths.payload_path)
        if old is not None:
            with pq.ParquetFile(old.payload_path) as parquet:
                previous = parquet.read().to_pylist()
            for row in previous:
                prior = json.loads(row["resolution"])
                old_statuses[prior["status"]] += 1
                current = by_candidate.get(prior["candidate_id"])
                if current is None:
                    raise ValueError(f"rebuild lost a prior candidate: {prior['candidate_id']}")
                if prior["review"] is not None and current.review != _convert_review(prior["review"]):
                    raise ValueError(f"rebuild lost or changed a confirmed review: {prior['candidate_id']}")
        for item in resolutions:
            statuses[item.status] += 1
            if item.review is not None:
                origins["reviewed_candidates"] += 1
                reviewed_events.add(item.action_id)
            elif item.source_record is not None:
                origins["source_candidates"] += 1
            else:
                reasons[item.reason] += 1
                unresolved.append(item.model_dump(mode="json"))
    FileSystem.write_bytes_atomic(output_dir / "unresolved.json", json.dumps(unresolved, ensure_ascii=False, indent=2).encode())
    report = {"version": "v1", "source_root": str(source_root), "storage_root": str(storage_root),
        "start": ordered_days[0], "end": ordered_days[-1], "partitions": len(ordered_days),
        "source_rows": source_rows, "implemented_rows": implemented_rows, "candidate_count": sum(statuses.values()),
        "previous_statuses": dict(old_statuses), "statuses": dict(statuses), **origins,
        "reviewed_events": len(reviewed_events), "committed_reviews": committed_reviews,
        "manual_reviews": manual_reviews, "unresolved_reasons": dict(sorted(reasons.items())),
        "original_inputs": "inputs.json", "unresolved": "unresolved.json"}
    report_path = output_dir / "report.json"
    FileSystem.write_bytes_atomic(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode())
    return report_path


def main(argv: Sequence[str] | None = None) -> int:
    """Run the isolated migration; argparse errors use 2 and execution errors use 1.

    Example:
        main(["--source-root", "/data/formal", "--output-dir", "/data/validation/actions"])
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = migrate_corporate_actions(source_root=args.source_root, output_dir=args.output_dir)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
