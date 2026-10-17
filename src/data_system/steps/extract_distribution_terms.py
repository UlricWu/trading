# filepath: src/data_system/steps/extract_distribution_terms.py
"""Build complete reviews from local PDFs and explicitly supplied reviewed facts."""

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

import pyarrow.parquet as pq
from pydantic import TypeAdapter
from pypdf import PdfReader

from src import logs
from src.access import meta
from src.data_system.brokers.cninfo import AnnouncementCollection
from src.data_system.context import DataContext
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord, resolve_corporate_actions
from src.data_system.distribution_announcements import AnnouncementText, ReviewExtractionManifest, extract_distribution_review
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class ExtractDistributionTermsStep:
    """Publish complete reviews; missing or ambiguous fields remain unresolved.

    Example:
        step = ExtractDistributionTermsStep(pm=pm, review_refs=(reviewed_file,))
        context = step.run(context)
    """

    pm: PathManager
    review_refs: tuple[Path, ...] = ()

    def run(self, context: DataContext) -> DataContext:
        """Extract from committed originals and validate manually reviewed source bindings.

        Example:
            context = step.run(DataContext(start="2026-09-01", end="2026-09-30", trade_dates=dates))
        """
        supplied: dict[str, CorporateActionReview] = {}
        for ref in self.review_refs:
            for review in TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(ref.read_bytes()):
                if review.candidate_id in supplied:
                    raise ValueError(f"duplicate supplied review: {review.candidate_id}")
                if review.record_date.isoformat() not in context.trade_dates:
                    raise ValueError(f"supplied review outside registration range: {review.candidate_id}")
                supplied[review.candidate_id] = review
        for day in context.trade_dates:
            source = self.pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
            loaded = meta.require(pm=self.pm, meta_path=source.meta_path, expected_payload_path=source.payload_path)
            with pq.ParquetFile(loaded.payload_path) as parquet:
                records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(parquet.read().to_pylist())
            groups: dict[str, list[DividendRecord]] = defaultdict(list)
            for row in records:
                if row.div_proc == "实施":
                    groups[row.candidate_id].append(row)
            collection_path = self.pm.raw_payload(broker="cninfo", source_name="corporate_action_announcements",
                trade_date=day, payload_file="announcements.json")
            collected = meta.find(pm=self.pm, meta_path=collection_path.parent / "meta.json",
                                  expected_payload_path=collection_path)
            if collected is None:
                if any(review.record_date.isoformat() == day for review in supplied.values()):
                    raise ValueError(f"supplied reviews require collected original evidence: {day}")
                logs.warning(f"⚠️ distribution extraction; record_date={day} reason=announcements_not_collected")
                continue
            collection = AnnouncementCollection.model_validate_json(collection_path.read_bytes())
            source_digest = sha256(loaded.payload_path.read_bytes()).hexdigest()
            if collection.dividend_sha256 != source_digest:
                raise ValueError(f"announcement collection dividend input changed: {day}")
            expected_symbols = {row.symbol for row in records
                                if row.div_proc == "实施" and row.ts_code.endswith((".SH", ".SZ"))}
            if set(collection.index_counts) != expected_symbols:
                raise ValueError(f"announcement collection lacks complete source-candidate coverage: {day}")
            manual_path = self.pm.raw_payload(broker="announcements", source_name="corporate_action_review_inputs",
                trade_date=day, payload_file="reviews.json")
            manual_meta = meta.find(pm=self.pm, meta_path=manual_path.parent / "meta.json", expected_payload_path=manual_path)
            manual = {} if manual_meta is None else {review.candidate_id: review for review in
                TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(manual_path.read_bytes())}
            additions = {key: review for key, review in supplied.items() if review.record_date.isoformat() == day}
            collection_digest = sha256(collection_path.read_bytes()).hexdigest()
            checked_collection = manual_path.parent / "collection.sha256"
            if (manual and not manual.keys() <= additions.keys()
                and (not checked_collection.exists() or checked_collection.read_text() != collection_digest)):
                raise ValueError(f"manual reviews require renewed verification for changed announcement collection: {day}")
            manual.update(additions)
            # Validate the whole manual set before replacing any committed input or output.
            resolve_corporate_actions(records, tuple(manual.values()))
            manually_bound = {candidate for review in manual.values()
                              for candidate in (review.candidate_id, *review.related_sources)}
            known = {f"{document.url}#sha256={document.sha256}" for document in collection.documents}
            for review in manual.values():
                if not all(ref in known for ref in review.evidence_refs):
                    raise ValueError(f"review evidence does not match collected PDF hashes: {review.candidate_id}")
            if additions:
                payload = TypeAdapter(tuple[CorporateActionReview, ...]).dump_json(tuple(manual[key] for key in sorted(manual)))
                if manual_meta is None or manual_path.read_bytes() != payload:
                    FileSystem.write_bytes_atomic(manual_path, payload)
                    meta.commit(pm=self.pm, payload_path=manual_path)
                if not checked_collection.exists() or checked_collection.read_text() != collection_digest:
                    FileSystem.write_bytes_atomic(checked_collection, collection_digest.encode())
                manual_meta = meta.require(pm=self.pm, meta_path=manual_path.parent / "meta.json")
            inputs = {str(loaded.payload_path.relative_to(self.pm.storage_root)): source_digest,
                      str(collection_path.relative_to(self.pm.storage_root)): collection_digest}
            if manual_meta is not None:
                inputs[str(manual_path.relative_to(self.pm.storage_root))] = sha256(manual_path.read_bytes()).hexdigest()
                inputs[str(checked_collection.relative_to(self.pm.storage_root))] = sha256(checked_collection.read_bytes()).hexdigest()
            for document in collection.documents:
                original = collection_path.parent / f"{document.announcement_id}.pdf"
                if sha256(original.read_bytes()).hexdigest() != document.sha256:
                    raise ValueError(f"announcement PDF digest changed: {original}")
            output = self.pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
                trade_date=day, payload_file="reviews.json")
            manifest_path = output.parent / "extraction.json"
            existing = meta.find(pm=self.pm, meta_path=output.parent / "meta.json", expected_payload_path=output)
            producer_sha256 = sha256(version("pypdf").encode() + b"".join(path.read_bytes() for path in (
                Path(__file__), Path(__file__).parents[1] / "distribution_announcements.py",
                Path(__file__).parents[1] / "corporate_actions.py",
            ))).hexdigest()
            if existing is not None and not manifest_path.exists():
                previous = TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(output.read_bytes())
                if any(review.candidate_id not in manually_bound for review in previous):
                    raise ValueError(f"existing reviews require explicit import before extraction: {output}")
            if existing is not None and manifest_path.exists():
                manifest = ReviewExtractionManifest.model_validate_json(manifest_path.read_bytes())
                if (manifest.inputs == inputs and manifest.producer_sha256 == producer_sha256
                    and manifest.reviews_sha256 == sha256(output.read_bytes()).hexdigest()):
                    logs.info(f"♻️ distribution extraction; record_date={day}")
                    continue
            documents = []
            for original in collection.documents:
                with (collection_path.parent / f"{original.announcement_id}.pdf").open("rb") as stream:
                    pages = tuple(page.extract_text() or "" for page in PdfReader(stream).pages)
                documents.append(AnnouncementText(**original.model_dump(exclude={"fetched_at"}), pages=pages))
            reviews = []
            issues = {}
            for candidate_id, group in sorted(groups.items()):
                if candidate_id in manual:
                    review = manual[candidate_id]
                    reviews.append(review)
                    continue
                if candidate_id in manually_bound:
                    continue
                if group[0].ts_code.endswith(".BJ"):
                    issues[candidate_id] = "unsupported_announcement_exchange"
                    continue
                extraction = extract_distribution_review(group, documents)
                if extraction.review is None:
                    issues[candidate_id] = extraction.reason
                else:
                    reviews.append(extraction.review)
            action_candidates: dict[str, list[str]] = defaultdict(list)
            for review in reviews:
                action_candidates[review.action_id].append(review.candidate_id)
            ambiguous = {candidate for candidates in action_candidates.values() if len(candidates) > 1 for candidate in candidates}
            for candidate in ambiguous:
                issues[candidate] = "shared_announcement_event_requires_review"
            reviews = [review for review in reviews if review.candidate_id not in ambiguous]
            resolve_corporate_actions(records, reviews)
            payload = TypeAdapter(tuple[CorporateActionReview, ...]).dump_json(tuple(reviews))
            manifest = ReviewExtractionManifest(inputs=inputs, producer_sha256=producer_sha256,
                reviews_sha256=sha256(payload).hexdigest(), issues=issues)
            FileSystem.write_bytes_atomic(output, payload)
            FileSystem.write_bytes_atomic(manifest_path, manifest.model_dump_json(indent=2).encode())
            meta.commit(pm=self.pm, payload_path=output, upstream_meta_path=collection_path.parent / "meta.json")
            logs.info(f"✅ distribution extraction; record_date={day} reviews={len(reviews)} unresolved={len(issues)}")
        return context
