# filepath: src/data_system/steps/extract_distribution_terms.py
"""Build complete reviews from local PDFs and explicitly supplied reviewed facts."""

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from importlib.metadata import version
from pathlib import Path
import re

import pyarrow.parquet as pq
from pydantic import TypeAdapter
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from src import logs
from src.access import meta
from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.announcement_publications import AnnouncementPublication
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
        if not context.trade_dates:
            return context
        producer_sha256 = sha256("\n".join((version("pypdf"), version("cryptography"))).encode() + b"".join(path.read_bytes() for path in (
            Path(__file__), Path(__file__).parents[1] / "distribution_announcements.py",
            Path(__file__).parents[1] / "corporate_actions.py",
            Path(__file__).parents[1] / "announcement_evidence.py",
            Path(__file__).parents[1] / "announcement_publications.py",
        ))).hexdigest()
        for day in context.trade_dates:
            source = self.pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
            loaded = meta.require(pm=self.pm, meta_path=source.meta_path, expected_payload_path=source.payload_path)
            with pq.ParquetFile(loaded.payload_path) as parquet:
                records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(parquet.read().to_pylist())
            groups: dict[str, list[DividendRecord]] = defaultdict(list)
            for row in records:
                if row.div_proc == "实施":
                    groups[row.candidate_id].append(row)
            source_digest = sha256(loaded.payload_path.read_bytes()).hexdigest()
            manual_path = self.pm.raw_payload(broker="announcements", source_name="corporate_action_review_inputs",
                trade_date=day, payload_file="reviews.json")
            manual_meta = meta.find(pm=self.pm, meta_path=manual_path.parent / "meta.json", expected_payload_path=manual_path)
            manual = {} if manual_meta is None else {review.candidate_id: review for review in
                TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(manual_path.read_bytes())}
            additions = {key: review for key, review in supplied.items() if review.record_date.isoformat() == day}
            origins = tuple(review.announcement_source for review in (*manual.values(), *additions.values())
                if review.announcement_source is not None)
            expected_symbols = {row.symbol for row in records if row.div_proc == "实施"}
            expected_symbols.update(origin.symbol for origin in origins)
            collections = {}
            collection_paths = {}
            missing = {}
            for provider in ("cninfo", "bse"):
                symbols = {row.symbol for row in records if row.div_proc == "实施"
                    and ("bse" if row.ts_code.endswith(".BJ") else "cninfo") == provider}
                symbols.update(origin.symbol for origin in origins if origin.source == provider)
                if not symbols:
                    continue
                path = self.pm.raw_payload(broker=provider, source_name="corporate_action_announcements",
                    trade_date=day, payload_file="announcements.json")
                collected = meta.find(pm=self.pm, meta_path=path.parent / "meta.json", expected_payload_path=path)
                if collected is None:
                    missing.update({symbol: provider for symbol in symbols})
                    continue
                collection = AnnouncementCollection.model_validate_json(path.read_bytes())
                if provider == "bse" and collection.query_scope != {"disclosureType": "5", "isNewThree": "", "xxfcbj": ""}:
                    raise ValueError(f"BSE announcement collection lacks complete market scope: {day}")
                if collection.dividend_sha256 != source_digest:
                    raise ValueError(f"announcement collection dividend input changed: {provider}/{day}")
                if not symbols <= collection.index_counts.keys() or not collection.index_counts.keys() <= expected_symbols:
                    raise ValueError(f"announcement collection lacks complete source-candidate coverage: {provider}/{day}")
                for name, digest in collection.request_sha256.items():
                    if sha256((path.parent / name).read_bytes()).hexdigest() != digest:
                        raise ValueError(f"announcement request evidence changed: {name}")
                collections[provider] = collection
                collection_paths[provider] = path
            collection_inputs = {str(path.relative_to(self.pm.storage_root)): sha256(path.read_bytes()).hexdigest()
                                 for path in collection_paths.values()}
            collection_digest = (next(iter(collection_inputs.values())) if len(collection_inputs) == 1 else
                sha256(json.dumps(collection_inputs, sort_keys=True).encode()).hexdigest())
            checked_collection = manual_path.parent / "collection.sha256"
            if (manual and not manual.keys() <= additions.keys()
                and (not checked_collection.exists() or checked_collection.read_text() != collection_digest)):
                raise ValueError(f"manual reviews require renewed verification for changed announcement collection: {day}")
            manual.update(additions)
            # Validate the whole manual set before replacing any committed input or output.
            resolve_corporate_actions(records, tuple(manual.values()))
            manually_bound = {candidate for review in manual.values()
                              for candidate in (review.candidate_id, *review.related_sources)}
            known = {f"{document.url}#sha256={document.sha256}" for collection in collections.values()
                     for document in collection.documents}
            known.update(f"{identity.evidence_url}#sha256={identity.evidence_sha256}"
                         for collection in collections.values() for identity in collection.security_identities)
            for review in manual.values():
                if not all(ref in known for ref in review.evidence_refs):
                    raise ValueError(f"review evidence does not match collected PDF hashes: {review.candidate_id}")
                if review.announcement_source is not None and not any(
                    document.sha256 == review.source_digest
                    and AnnouncementPublication(source=provider, **document.model_dump(exclude={"sha256", "fetched_at"}))
                        == review.announcement_source
                    for provider, collection in collections.items() for document in collection.documents
                ):
                    raise ValueError(f"independent announcement source differs from collected evidence: {review.candidate_id}")
                for terms in review.terms:
                    if terms.published_ts_us is not None and not any(
                        document.published_ts_us == terms.published_ts_us
                        and document.published_date == terms.published_date
                        and f"{document.url}#sha256={document.sha256}" in review.evidence_refs
                        for collection in collections.values() for document in collection.documents
                    ):
                        raise ValueError(f"review publication time lacks collected index evidence: {review.candidate_id}")
            for provider, collection in collections.items():
                for document in collection.documents:
                    original = collection_paths[provider].parent / f"{document.announcement_id}.pdf"
                    if sha256(original.read_bytes()).hexdigest() != document.sha256:
                        raise ValueError(f"announcement PDF digest changed: {original}")
            if additions:
                payload = TypeAdapter(tuple[CorporateActionReview, ...]).dump_json(tuple(manual[key] for key in sorted(manual)))
                if manual_meta is not None and (manual_path.read_bytes() != payload
                    or not checked_collection.exists() or checked_collection.read_text() != collection_digest):
                    originals = {path.name: path.read_bytes() for path in
                        (manual_path, manual_path.parent / "meta.json", checked_collection) if path.exists()}
                    archive_id = sha256(b"".join(originals[key] for key in sorted(originals))).hexdigest()
                    for name, original in originals.items():
                        FileSystem.write_bytes_atomic(manual_path.parent / "history" / archive_id / name, original)
                if manual_meta is None or manual_path.read_bytes() != payload:
                    FileSystem.write_bytes_atomic(manual_path, payload)
                    meta.commit(pm=self.pm, payload_path=manual_path)
                if not checked_collection.exists() or checked_collection.read_text() != collection_digest:
                    FileSystem.write_bytes_atomic(checked_collection, collection_digest.encode())
                manual_meta = meta.require(pm=self.pm, meta_path=manual_path.parent / "meta.json")
            inputs = {str(loaded.payload_path.relative_to(self.pm.storage_root)): source_digest, **collection_inputs}
            if manual_meta is not None:
                inputs[str(manual_path.relative_to(self.pm.storage_root))] = sha256(manual_path.read_bytes()).hexdigest()
                inputs[str(checked_collection.relative_to(self.pm.storage_root))] = sha256(checked_collection.read_bytes()).hexdigest()
            output = self.pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
                trade_date=day, payload_file="reviews.json")
            manifest_path = output.parent / "extraction.json"
            existing = meta.inspect_output(pm=self.pm, meta_path=output.parent / "meta.json", expected_payload_path=output)
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
            unreadable: dict[tuple[str, str], list[str]] = defaultdict(list)
            identities = tuple(identity for collection in collections.values() for identity in collection.security_identities)
            for provider, collection in collections.items():
                source_symbols = {row.symbol for row in records if row.div_proc == "实施"
                    and ("bse" if row.ts_code.endswith(".BJ") else "cninfo") == provider}
                source_symbols.update(row.published_symbol for row in collection.security_identities)
                for original in collection.documents:
                    if original.symbol not in source_symbols:
                        continue
                    if not re.search(r"实施|更正|补充|修订|调整|取消|终止", original.title):
                        continue
                    try:
                        with (collection_paths[provider].parent / f"{original.announcement_id}.pdf").open("rb") as stream:
                            pages = tuple(page.extract_text() or "" for page in PdfReader(stream).pages)
                    except (PyPdfError, UnicodeDecodeError) as error:
                        unreadable[provider, original.symbol].append(
                            f"pdf_text_unreadable:{provider}:{original.announcement_id}:{type(error).__name__}")
                        logs.warning(f"⚠️ distribution PDF requires review; record_date={day} provider={provider} "
                            f"announcement={original.announcement_id} error={error}")
                        continue
                    if not any(page.strip() for page in pages):
                        unreadable[provider, original.symbol].append(
                            f"pdf_text_unreadable:{provider}:{original.announcement_id}:empty_text")
                        continue
                    documents.append(AnnouncementText(source=provider, **original.model_dump(exclude={"fetched_at"}), pages=pages))
            reviews = [review for review in manual.values() if review.announcement_source is not None]
            issues = {}
            for candidate_id, group in sorted(groups.items()):
                if candidate_id in manual:
                    review = manual[candidate_id]
                    reviews.append(review)
                    continue
                if candidate_id in manually_bound:
                    continue
                if group[0].symbol in missing:
                    issues[candidate_id] = f"announcements_not_collected:{missing[group[0].symbol]}"
                    continue
                symbol = group[0].symbol
                provider = "bse" if group[0].ts_code.endswith(".BJ") else "cninfo"
                aliases = {symbol, *(identity.published_symbol for identity in identities if identity.symbol == symbol)}
                errors = sorted(reason for alias in aliases for reason in unreadable.get((provider, alias), ()))
                if errors:
                    issues[candidate_id] = ";".join(errors)
                    continue
                extraction = extract_distribution_review(group, documents, identities)
                if extraction.review is None:
                    issues[candidate_id] = extraction.reason
                else:
                    reviews.append(extraction.review)
            action_candidates: dict[str, list[str]] = defaultdict(list)
            for review in reviews:
                action_candidates[review.action_id].append(review.candidate_id)
            ambiguous = {candidate for candidates in action_candidates.values() if len(candidates) > 1 for candidate in candidates}
            if ambiguous & manual.keys():
                raise ValueError(f"reviewed announcement requires explicit source association: {day}")
            for candidate in ambiguous:
                issues[candidate] = "shared_announcement_event_requires_review"
            reviews = [review for review in reviews if review.candidate_id not in ambiguous]
            resolve_corporate_actions(records, reviews)
            payload = TypeAdapter(tuple[CorporateActionReview, ...]).dump_json(tuple(reviews))
            manifest = ReviewExtractionManifest(inputs=inputs, producer_sha256=producer_sha256,
                reviews_sha256=sha256(payload).hexdigest(), issues=issues)
            if existing is not None:
                originals = {path.name: path.read_bytes() for path in
                    (output, output.parent / "meta.json", manifest_path) if path.exists()}
                archive_id = sha256(b"".join(originals[key] for key in sorted(originals))).hexdigest()
                for name, original in originals.items():
                    FileSystem.write_bytes_atomic(output.parent / "history" / archive_id / name, original)
            FileSystem.write_bytes_atomic(output, payload)
            FileSystem.write_bytes_atomic(manifest_path, manifest.model_dump_json(indent=2).encode())
            meta.commit(pm=self.pm, payload_path=output, upstream_meta_path=source.meta_path)
            logs.info(f"✅ distribution extraction; record_date={day} reviews={len(reviews)} unresolved={len(issues)}")
        return context
