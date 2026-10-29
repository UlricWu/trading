# filepath: src/data_system/announcement_maintenance.py
"""Associate committed publication evidence with local historical registration partitions."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Literal

import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, TypeAdapter

from src.access import meta
from src.data_system.announcement_evidence import AnnouncementCollection, SecurityIdentity
from src.data_system.announcement_publications import (
    AnnouncementPublication, PublicationCoverage, PublicationIndex, is_distribution_evidence,
)
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord
from src.utils.path import PathManager


class AnnouncementRefresh(BaseModel):
    """The source evidence that must be refreshed for one original registration day.

    Example:
        refresh = prepare_announcement_updates(path_manager=pm, start=date(2026, 9, 19),
            end=date(2026, 9, 20)).refreshes[0]
        assert refresh.announcement_start <= refresh.announcement_end
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["cninfo", "bse"]
    registration_date: str
    announcement_start: date
    announcement_end: date
    publications: tuple[AnnouncementPublication, ...]


class AnnouncementUpdates(BaseModel):
    """Bound publication inputs, affected registrations and explicitly unmatched evidence.

    Example:
        updates = prepare_announcement_updates(path_manager=pm, start=date(2026, 9, 19), end=date(2026, 9, 20))
        print(len(updates.refreshes))
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    publication_start: date
    publication_end: date
    refreshes: tuple[AnnouncementRefresh, ...]
    unmatched_publications: tuple[AnnouncementPublication, ...]
    input_refs: tuple[str, ...]


def prepare_announcement_updates(
    *, path_manager: PathManager, start: date, end: date, identity_refs: Sequence[Path] = (),
    review_refs: Sequence[Path] = (),
) -> AnnouncementUpdates:
    """Verify committed public indexes and associate their source identities with local candidates.

    Unclassified revisions conservatively refresh the issuer's known registrations;
    their mere presence never authorizes an account distribution.

    Example:
        updates = prepare_announcement_updates(path_manager=pm, start=date(2026, 9, 19), end=date(2026, 9, 20))
    """
    inputs = []
    publications = []
    for provider in ("cninfo", "bse"):
        root = path_manager.raw_source_dir(broker=provider, source_name="corporate_action_publications")
        for path in sorted(root.glob("trade_date=*/index.json")):
            day = date.fromisoformat(path.parent.name.removeprefix("trade_date="))
            if day > end:
                continue
            meta.require(pm=path_manager, meta_path=path.parent / "meta.json", expected_payload_path=path)
            index = PublicationIndex.model_validate_json(path.read_bytes())
            coverage = PublicationCoverage.model_validate_json((path.parent / "coverage.json").read_bytes())
            if coverage.index_count != len(index.announcements):
                raise ValueError(f"publication coverage changed: {provider}/{day}")
            inputs.append(str(path.relative_to(path_manager.storage_root)))
            publications.extend(row for row in index.announcements if is_distribution_evidence(row.title))
    records_by_day: dict[str, tuple[DividendRecord, ...]] = {}
    reviews_by_day: dict[str, tuple[CorporateActionReview, ...]] = {}
    security_dates: dict[tuple[str, str], set[str]] = defaultdict(set)
    collections: dict[tuple[str, str], AnnouncementCollection] = {}
    identities = []
    supplied = {}
    for ref in review_refs:
        for review in TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(ref.read_bytes()):
            supplied[review.candidate_id] = review
        inputs.append(str(ref))
    for ref in identity_refs:
        identities.extend(TypeAdapter(tuple[SecurityIdentity, ...]).validate_json(ref.read_bytes()))
        inputs.append(str(ref))
    for source_path in sorted(path_manager.processed_version_dir(dataset_name="dividend", version="v1").glob(
        "trade_date=*/data.parquet",
    )):
        meta.require(pm=path_manager, meta_path=source_path.parent / "meta.json", expected_payload_path=source_path)
        registration = source_path.parent.name.removeprefix("trade_date=")
        inputs.append(str(source_path.relative_to(path_manager.storage_root)))
        with pq.ParquetFile(source_path) as parquet:
            records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(parquet.read().to_pylist())
        records_by_day[registration] = records
        review_path = path_manager.raw_payload(broker="announcements", source_name="corporate_action_review_inputs",
            trade_date=registration, payload_file="reviews.json")
        reviewed = meta.find(pm=path_manager, meta_path=review_path.parent / "meta.json", expected_payload_path=review_path)
        reviews = {} if reviewed is None else {review.candidate_id: review for review in
            TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(review_path.read_bytes())}
        if reviewed is not None:
            inputs.append(str(review_path.relative_to(path_manager.storage_root)))
        reviews.update({key: review for key, review in supplied.items() if review.record_date.isoformat() == registration})
        reviews_by_day[registration] = tuple(reviews.values())
        for review in reviews.values():
            origin = review.announcement_source
            if origin is not None:
                security_dates[(origin.source, origin.symbol)].add(registration)
        for row in records:
            if row.div_proc == "实施":
                security_dates[("bse" if row.ts_code.endswith(".BJ") else "cninfo", row.symbol)].add(registration)
        for provider in ("cninfo", "bse"):
            path = path_manager.raw_payload(broker=provider, source_name="corporate_action_announcements",
                trade_date=registration, payload_file="announcements.json")
            if meta.find(pm=path_manager, meta_path=path.parent / "meta.json", expected_payload_path=path) is None:
                continue
            collection = AnnouncementCollection.model_validate_json(path.read_bytes())
            collections[(provider, registration)] = collection
            identities.extend(collection.security_identities)
            inputs.append(str(path.relative_to(path_manager.storage_root)))
    for identity in identities:
        for provider in ("cninfo", "bse"):
            security_dates[(provider, identity.published_symbol)].update(security_dates.get((provider, identity.symbol), ()))
    affected: dict[tuple[str, str], list[AnnouncementPublication]] = defaultdict(list)
    unmatched = []
    for publication in publications:
        if publication.source == "cninfo" and security_dates.get(("bse", publication.symbol)):
            continue
        registrations = security_dates.get((publication.source, publication.symbol), ())
        if not registrations:
            unmatched.append(publication)
        for registration in registrations:
            affected[(publication.source, registration)].append(publication)
    refreshes = []
    for (provider, registration), observed in sorted(affected.items()):
        records = records_by_day[registration]
        starts = [row.record_date for row in records]
        starts.extend(value for row in records for value in (row.ann_date, row.imp_ann_date) if value is not None)
        ends = [row.record_date for row in records]
        ends.extend(value for row in records for value in (row.ex_date, row.pay_date, row.div_listdate) if value is not None)
        for review in reviews_by_day[registration]:
            if review.announcement_source is not None:
                starts.append(review.announcement_source.published_date)
                ends.extend(value for value in (review.record_date, review.ex_date, review.pay_date, review.div_listdate)
                    if value is not None)
        saved = collections.get((provider, registration))
        if saved is not None:
            starts.append(saved.announcement_start)
            ends.append(saved.announcement_end)
        starts.extend(row.published_date for row in observed)
        ends.extend(row.published_date for row in observed)
        announcement_start = min(starts)
        announcement_end = min(max(ends), end)
        refreshes.append(AnnouncementRefresh(provider=provider, registration_date=registration,
            announcement_start=announcement_start, announcement_end=announcement_end, publications=tuple(observed)))
    return AnnouncementUpdates(publication_start=start, publication_end=end, refreshes=tuple(refreshes),
        unmatched_publications=tuple(unmatched), input_refs=tuple(inputs))
