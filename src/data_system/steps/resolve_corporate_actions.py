# filepath: src/data_system/steps/resolve_corporate_actions.py
"""Publish corporate-action dispositions with source and announcement lineage."""
from __future__ import annotations

from dataclasses import dataclass
import json

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import TypeAdapter

from src import logs
from src.access import meta
from src.data_system.context import DataContext
from src.data_system.corporate_actions import CorporateActionReview, DividendRecord, resolve_corporate_actions
from src.data_system.steps._partition import _publish_parquet_object
from src.data_system.distribution_announcements import ReviewExtractionManifest
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class ResolveCorporateActionsStep:
    """Resolve local dividend/v1 rows and optional committed announcement reviews.

    Example:
        context = ResolveCorporateActionsStep(pm).run(context)
    """

    pm: PathManager

    def run(self, context: DataContext) -> DataContext:
        """Publish every candidate's disposition, including explicitly unresolved facts.

        Example:
            step.run(DataContext(start="2024-07-23", end="2024-07-23", trade_dates=("2024-07-23",)))
        """
        for day in context.trade_dates:
            source = self.pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
            loaded = meta.require(pm=self.pm, meta_path=source.meta_path, expected_payload_path=source.payload_path)
            review_meta = self.pm.raw_meta(broker="announcements", source_name="corporate_action_reviews", trade_date=day)
            reviewed = meta.find(pm=self.pm, meta_path=review_meta)
            reviews = ()
            inputs = [str(source.meta_path.relative_to(self.pm.storage_root))]
            if reviewed is not None:
                review_payload = reviewed.payload_path.read_bytes()
                reviews = TypeAdapter(tuple[CorporateActionReview, ...]).validate_json(review_payload)
                inputs.append(str(review_meta.relative_to(self.pm.storage_root)))
            extraction = None
            if reviewed is not None and (extraction_path := reviewed.payload_path.parent / "extraction.json").exists():
                extraction_payload = extraction_path.read_bytes()
                extraction = ReviewExtractionManifest.model_validate_json(extraction_payload)
                inputs.append(str(extraction_path.relative_to(self.pm.storage_root)))
            paths = self.pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=day)
            existing = meta.inspect_output(pm=self.pm, meta_path=paths.meta_path, expected_payload_path=paths.payload_path)
            with pq.ParquetFile(loaded.payload_path) as parquet:
                records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(parquet.read().to_pylist())
            if any(row.record_date.isoformat() != day for row in records):
                raise ValueError(f"dividend source registration does not match partition: {day}")
            resolutions = resolve_corporate_actions(records, reviews)
            if extraction is not None:
                dispositions = []
                for item in resolutions:
                    issue = extraction.issues.get(item.candidate_id)
                    if item.review is None and issue is not None:
                        conflict = issue.split(":", 1)[0] in {
                            "special_beneficiary_requires_review", "separate_revision_requires_review",
                            "distribution_amount_conflict", "phase_date_revision_requires_review",
                            "same_day_revision_order_unknown", "announcement_registration_mismatch",
                            "reference_result_conflict",
                            "unsupported_announcement_timeline", "shared_announcement_event_requires_review",
                            "previous_review_requires_confirmation",
                            "compensation_requires_review",
                            "ordinary_cash_entitlement_unconfirmed", "implementation_stage_unconfirmed",
                        }
                        if item.status == "unresolved" or conflict:
                            item = item.model_copy(update={"status": "unresolved", "source_record": None, "reason": issue})
                    dispositions.append(item)
                resolutions = tuple(dispositions)
            schema = pa.schema([("symbol", pa.string()), ("record_date", pa.string()), ("resolution", pa.string())],
                metadata={b"corporate_action_inputs": json.dumps(inputs, sort_keys=True).encode()})
            table = pa.Table.from_pylist([{"symbol": item.symbol, "record_date": day,
                "resolution": item.model_dump_json()} for item in resolutions], schema=schema)
            if existing is not None:
                with pq.ParquetFile(existing.payload_path) as parquet:
                    if parquet.read().equals(table, check_metadata=True):
                        logs.info(f"♻️ corporate actions; trade_date={day}")
                        continue
            _publish_parquet_object(pm=self.pm, paths=paths, table=table, upstream_meta_path=source.meta_path)
            unresolved = sum(item.status == "unresolved" for item in resolutions)
            logs.info(f"✅ corporate actions; trade_date={day} groups={len(resolutions)} unresolved={unresolved}")
        return context
