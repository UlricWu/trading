# filepath: src/data_system/steps/extract_distribution_terms.py
"""Build dated event facts from local PDFs and explicitly supplied reviews."""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
import re

import pyarrow.parquet as pq
from pydantic import TypeAdapter
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from src import logs
from src.access import meta
from src.data_system.announcement_evidence import AnnouncementCollection
from src.data_system.announcement_publications import AnnouncementPublication, is_distribution_evidence
from src.data_system.context import DataContext
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord, resolve_corporate_actions
from src.data_system.distribution_announcements import AnnouncementText, ReviewExtractionManifest, extract_distribution_review
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class ExtractDistributionTermsStep:
    """Publish confirmed stage facts; unconfirmed gaps and ambiguities remain unresolved.

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
        for day in context.trade_dates:
            source = self.pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
            loaded = meta.require(pm=self.pm, meta_path=source.meta_path, expected_payload_path=source.payload_path)
            with pq.ParquetFile(loaded.payload_path) as parquet:
                records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(parquet.read().to_pylist())
            groups: dict[str, list[DividendRecord]] = defaultdict(list)
            for row in records:
                if row.div_proc == "实施":
                    groups[row.candidate_id].append(row)
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
                if not symbols <= collection.index_counts.keys() or not collection.index_counts.keys() <= expected_symbols:
                    raise ValueError(f"announcement collection lacks complete source-candidate coverage: {provider}/{day}")
                collections[provider] = collection
                collection_paths[provider] = path
            manual.update(additions)
            # Validate the whole manual set before replacing any committed input or output.
            resolve_corporate_actions(records, tuple(manual.values()))
            manually_bound = {candidate for review in manual.values()
                              for candidate in (review.candidate_id, *review.related_sources)}
            known = {document.url for collection in collections.values()
                     for document in collection.documents}
            known.update(identity.evidence_url
                         for collection in collections.values() for identity in collection.security_identities)
            for review in manual.values():
                if not all(ref in known for ref in review.evidence_refs):
                    raise ValueError(f"review evidence does not match collected announcements: {review.candidate_id}")
                if review.announcement_source is not None and not any(
                    AnnouncementPublication(source=provider, **document.model_dump(exclude={"sha256", "fetched_at"}))
                        == review.announcement_source
                    for provider, collection in collections.items() for document in collection.documents
                ):
                    raise ValueError(f"independent announcement source differs from collected evidence: {review.candidate_id}")
                for terms in review.terms:
                    if terms.published_ts_us is not None and not any(
                        document.published_ts_us == terms.published_ts_us
                        and document.published_date == terms.published_date
                        and document.url in review.evidence_refs
                        for collection in collections.values() for document in collection.documents
                    ):
                        raise ValueError(f"review publication time lacks collected index evidence: {review.candidate_id}")
            if additions:
                payload = TypeAdapter(tuple[CorporateActionReview, ...]).dump_json(tuple(manual[key] for key in sorted(manual)))
                if manual_meta is None or manual_path.read_bytes() != payload:
                    if manual_meta is not None:
                        archive = manual_path.parent / "history" / uuid4().hex
                        for original in (manual_path, manual_path.parent / "meta.json"):
                            FileSystem.write_bytes_atomic(archive / original.name, original.read_bytes())
                    FileSystem.write_bytes_atomic(manual_path, payload)
                    meta.commit(pm=self.pm, payload_path=manual_path)
                manual_meta = meta.require(pm=self.pm, meta_path=manual_path.parent / "meta.json")
            inputs = [str(path.relative_to(self.pm.storage_root))
                      for path in (loaded.payload_path, *collection_paths.values())]
            if manual_meta is not None:
                inputs.append(str(manual_path.relative_to(self.pm.storage_root)))
            output = self.pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
                trade_date=day, payload_file="reviews.json")
            manifest_path = output.parent / "extraction.json"
            existing = meta.inspect_output(pm=self.pm, meta_path=output.parent / "meta.json", expected_payload_path=output)
            previous = (() if existing is None else
                TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(output.read_bytes()))
            previous_manifest = None
            if existing is not None and not manifest_path.exists():
                if any(review.candidate_id not in manually_bound for review in previous):
                    raise ValueError(f"existing reviews require explicit import before extraction: {output}")
            if existing is not None and manifest_path.exists():
                previous_manifest = ReviewExtractionManifest.model_validate_json(manifest_path.read_bytes())
            documents = []
            unreadable = defaultdict(list)
            identities = tuple(identity for collection in collections.values() for identity in collection.security_identities)
            for provider, collection in collections.items():
                source_symbols = {row.symbol for row in records if row.div_proc == "实施"
                    and row.candidate_id not in manually_bound
                    and ("bse" if row.ts_code.endswith(".BJ") else "cninfo") == provider}
                source_symbols.update(row.published_symbol for row in collection.security_identities
                    if row.symbol in source_symbols)
                for original in collection.documents:
                    if original.symbol not in source_symbols:
                        continue
                    implementation_dates = {row.imp_ann_date for row in records
                        if row.symbol == original.symbol or any(identity.symbol == row.symbol
                            and identity.published_symbol == original.symbol for identity in identities)}
                    if not is_distribution_evidence(original.title) and original.published_date not in implementation_dates:
                        continue
                    try:
                        with (collection_paths[provider].parent / f"{original.announcement_id}.pdf").open("rb") as stream:
                            pages = tuple(page.extract_text() or "" for page in PdfReader(stream).pages)
                    except (PyPdfError, UnicodeDecodeError) as error:
                        unreadable[provider, original.symbol].append((original,
                            f"pdf_text_unreadable:{provider}:{original.announcement_id}:{type(error).__name__}"))
                        logs.warning(f"⚠️ distribution PDF requires review; record_date={day} provider={provider} "
                            f"announcement={original.announcement_id} error={error}")
                        continue
                    if not any(page.strip() for page in pages):
                        unreadable[provider, original.symbol].append((original,
                            f"pdf_text_unreadable:{provider}:{original.announcement_id}:empty_text"))
                        continue
                    documents.append(AnnouncementText(source=provider, **original.model_dump(exclude={"sha256", "fetched_at"}), pages=pages))
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
                extraction = extract_distribution_review(group, documents, identities)
                implementation_dates = {row.imp_ann_date for row in group if row.imp_ann_date is not None}
                errors = []
                for alias in aliases:
                    for original, reason in unreadable.get((provider, alias), ()):
                        if (extraction.review is not None and re.search(r"法律意见|专项意见", original.title)
                            and not re.search(r"更正|补充|修订|取消|终止", original.title)):
                            continue
                        years = re.findall(r"(20\d{2})年", original.title)
                        if (original.published_date not in implementation_dates and years
                            and str(group[0].end_date.year) not in years):
                            continue
                        periods = re.findall(r"20\d{2}年?(上?半年度|年度|第[一二三四]季度|前三季度)", original.title)
                        period_months = {"半年度": 6, "上半年度": 6, "年度": 12, "第一季度": 3,
                            "第二季度": 6, "第三季度": 9, "前三季度": 9, "第四季度": 12}
                        if (original.published_date not in implementation_dates and periods
                            and all(period_months[period] != group[0].end_date.month for period in periods)):
                            continue
                        if is_distribution_evidence(original.title) or extraction.review is None:
                            errors.append(reason)
                if errors:
                    issues[candidate_id] = ";".join(sorted(errors))
                    continue
                if extraction.review is None:
                    issues[candidate_id] = extraction.reason
                else:
                    reviews.append(extraction.review)
            action_reviews: dict[str, list[CorporateActionReview]] = defaultdict(list)
            for review in reviews:
                action_reviews[review.action_id].append(review)
            reviews = []
            for associated in action_reviews.values():
                first = min(associated, key=lambda review: review.candidate_id)
                facts = {"candidate_id", "related_sources", "evidence_refs", "reason"}
                if any(review.model_dump(exclude=facts) != first.model_dump(exclude=facts) for review in associated):
                    if any(review.candidate_id in manual for review in associated):
                        raise ValueError(f"reviewed announcement requires explicit source association: {day}")
                    for review in associated:
                        issues[review.candidate_id] = "shared_announcement_event_requires_review"
                    continue
                candidates = {candidate for review in associated
                    for candidate in (review.candidate_id, *review.related_sources)}
                reviews.append(first.model_copy(update={
                    "related_sources": tuple(sorted(candidates - {first.candidate_id})),
                    "evidence_refs": tuple(sorted({ref for review in associated for ref in review.evidence_refs})),
                }) if len(associated) > 1 else first)
            bound = {candidate for review in reviews for candidate in (review.candidate_id, *review.related_sources)}
            previously_bound = {candidate for review in previous
                                for candidate in (review.candidate_id, *review.related_sources)}
            if previous_manifest is not None:
                previously_bound.update(candidate for candidate, reason in previous_manifest.issues.items()
                    if reason.startswith("previous_review_requires_confirmation:"))
            for candidate in previously_bound - bound:
                if candidate not in groups:
                    raise ValueError(f"previous review lost its source and requires confirmation: {candidate}")
                issues[candidate] = "previous_review_requires_confirmation:" + issues.get(candidate, "missing_review")
            resolve_corporate_actions(records, reviews)
            payload = TypeAdapter(tuple[CorporateActionReview, ...]).dump_json(tuple(reviews))
            manifest = ReviewExtractionManifest(inputs=tuple(inputs), issues=issues)
            if existing is not None and output.read_bytes() == payload and previous_manifest == manifest:
                logs.info(f"♻️ distribution extraction; record_date={day}")
                continue
            if existing is not None and output.read_bytes() != payload:
                archive = output.parent / "history" / uuid4().hex
                for original in (output, output.parent / "meta.json", manifest_path):
                    if original.exists():
                        FileSystem.write_bytes_atomic(archive / original.name, original.read_bytes())
            FileSystem.write_bytes_atomic(output, payload)
            FileSystem.write_bytes_atomic(manifest_path, manifest.model_dump_json(indent=2).encode())
            meta.commit(pm=self.pm, payload_path=output, upstream_meta_path=source.meta_path)
            logs.info(f"✅ distribution extraction; record_date={day} reviews={len(reviews)} unresolved={len(issues)}")
        return context
