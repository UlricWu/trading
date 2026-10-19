# filepath: src/data_system/steps/resolve_security_status.py
"""Publish reviewed daily security states without rewriting source market facts."""

from dataclasses import dataclass
from hashlib import sha256
import json

import pyarrow as pa
import pyarrow.parquet as pq

from src import logs
from src.access import meta
from src.data_system.context import DataContext
from src.data_system.security_status import SecurityStatusReviews
from src.data_system.steps._partition import _publish_parquet_object
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class ResolveSecurityStatusStep:
    """Verify evidence and original inputs, then publish their independent status.

    Example:
        ResolveSecurityStatusStep(pm=pm).run(DataContext(start="2026-09-15",
            end="2026-09-15", trade_dates=("2026-09-15",)))
    """

    pm: PathManager

    def run(self, context: DataContext) -> DataContext:
        """Keep unreviewed dates unchanged and invalidate stale reviewed partitions.

        Example:
            context = step.run(context)
        """
        for day in context.trade_dates:
            review_path = self.pm.raw_payload(broker="announcements", source_name="security_status_reviews",
                trade_date=day, payload_file="reviews.json")
            loaded = meta.find(pm=self.pm, meta_path=review_path.parent / "meta.json",
                expected_payload_path=review_path)
            if loaded is None:
                continue
            payload = loaded.payload_path.read_bytes()
            reviewed = SecurityStatusReviews.model_validate_json(payload)
            if any(review.trade_date.isoformat() != day for review in reviewed.reviews):
                raise ValueError(f"security status review date differs from partition: {day}")
            for name, digest in reviewed.source_digests.items():
                source = self.pm.processed_object(dataset_name=name, version="v1", trade_date=day)
                original = meta.require(pm=self.pm, meta_path=source.meta_path,
                    expected_payload_path=source.payload_path)
                if sha256(original.payload_path.read_bytes()).hexdigest() != digest:
                    raise ValueError(f"security status source digest changed: {name}/{day}")
            for document in reviewed.documents:
                original_pdf = review_path.parent / f"{document.announcement_id}.pdf"
                if sha256(original_pdf.read_bytes()).hexdigest() != document.sha256:
                    raise ValueError(f"security status PDF digest changed: {document.announcement_id}")
            inputs = {**reviewed.source_digests, "reviews": sha256(payload).hexdigest()}
            paths = self.pm.processed_object(dataset_name="security_status", version="v1", trade_date=day)
            existing = meta.inspect_output(pm=self.pm, meta_path=paths.meta_path, expected_payload_path=paths.payload_path)
            if existing is not None:
                with pq.ParquetFile(existing.payload_path) as parquet:
                    previous = json.loads(parquet.schema_arrow.metadata[b"security_status_inputs"])
                if previous == inputs:
                    logs.info(f"♻️ security status; trade_date={day}")
                    continue
            schema = pa.schema([("symbol", pa.string()), ("trade_date", pa.string()),
                ("resolution", pa.string())], metadata={b"security_status_inputs": json.dumps(inputs, sort_keys=True).encode()})
            table = pa.Table.from_pylist([{"symbol": review.symbol, "trade_date": day,
                "resolution": review.model_dump_json()} for review in sorted(reviewed.reviews, key=lambda row: row.ts_code)],
                schema=schema)
            _publish_parquet_object(pm=self.pm, paths=paths, table=table,
                upstream_meta_path=review_path.parent / "meta.json")
            logs.info(f"✅ security status; trade_date={day} reviews={len(reviewed.reviews)}")
        return context
