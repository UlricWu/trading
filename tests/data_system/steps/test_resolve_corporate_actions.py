# filepath: tests/data_system/steps/test_resolve_corporate_actions.py
"""Publication, review lineage and reuse at the corporate-action data boundary."""
from copy import deepcopy
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.data_system.context import DataContext
from src.data_system.steps.resolve_corporate_actions import ResolveCorporateActionsStep
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


@pytest.fixture
def source(tmp_path: Path) -> tuple[PathManager, DataContext, dict[str, object]]:
    case = json.loads((Path(__file__).parents[2] / "fixtures/corporate_actions/verified_distributions.json").read_text())[1]
    day = case["review"]["record_date"]
    pm = PathManager(tmp_path)
    paths = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=paths.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=paths.payload_path)
    return pm, DataContext(start=day, end=day, trade_dates=(day,)), case


def test_publication_preserves_unresolved_groups_and_review_changes_invalidate_reuse(
    source: tuple[PathManager, DataContext, dict[str, object]],
) -> None:
    pm, context, case = source
    step = ResolveCorporateActionsStep(pm)
    step.run(context)
    access = Access(pm=pm, processed_version="v1")
    unresolved, = access.corporate_actions(record_date=context.start)
    assert unresolved.status == "unresolved"
    paths = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start)
    before = paths.payload_path.stat().st_mtime_ns
    step.run(context)
    assert paths.payload_path.stat().st_mtime_ns == before
    review_path = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
                                 trade_date=context.start, payload_file="reviews.json")
    FileSystem.write_bytes_atomic(review_path, json.dumps([case["review"]]).encode())
    meta.commit(pm=pm, payload_path=review_path)
    step.run(context)
    resolved, = access.corporate_actions(record_date=context.start)
    assert resolved.status == "resolved"
    assert str(resolved.review.terms[0].cash_per_share) == "0.3158"
    assert str(resolved.review.terms[0].reference_cash_per_share) == "0.28"
    with pq.ParquetFile(paths.payload_path) as parquet:
        lineage = json.loads(parquet.schema_arrow.metadata[b"corporate_action_inputs"])
    assert set(lineage) == {
        f"processed/dividend/v1/trade_date={context.start}/meta.json",
        f"raw/announcements/corporate_action_reviews/trade_date={context.start}/meta.json",
    }
    assert all(len(digest) == 64 for digest in lineage.values())
    before = paths.payload_path.read_bytes()
    changed = deepcopy(case["review"])
    changed["source_digest"] = "0" * 64
    FileSystem.write_bytes_atomic(review_path, json.dumps([changed]).encode())
    meta.commit(pm=pm, payload_path=review_path)
    with pytest.raises(ValueError, match="source digest changed"):
        step.run(context)
    assert paths.payload_path.read_bytes() == before


def test_committed_empty_source_is_not_missing_coverage(source: tuple[PathManager, DataContext, dict[str, object]]) -> None:
    pm, context, case = source
    source_paths = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    write_parquet_atomic(output_file=source_paths.payload_path, table=pa.Table.from_pylist(case["records"]).slice(0, 0))
    meta.commit(pm=pm, payload_path=source_paths.payload_path)
    access = Access(pm=pm, processed_version="v1")
    assert access.corporate_actions(record_date=context.start) is None
    ResolveCorporateActionsStep(pm).run(context)
    assert access.corporate_actions(record_date=context.start) == ()
    source_paths.payload_path.unlink()
    with pytest.raises((FileNotFoundError, RuntimeError)):
        access.corporate_actions(record_date=context.start)


def test_access_requires_all_candidates_of_an_associated_event(tmp_path: Path) -> None:
    cases = json.loads((Path(__file__).parents[2] / "fixtures/corporate_actions/verified_distributions.json").read_text())
    case = next(item for item in cases if item["records"][0]["symbol"] == "000550")
    pm = PathManager(tmp_path)
    day = case["review"]["record_date"]
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=source.payload_path)
    review_path = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=day, payload_file="reviews.json")
    FileSystem.write_bytes_atomic(review_path, json.dumps([case["review"]]).encode())
    meta.commit(pm=pm, payload_path=review_path)
    ResolveCorporateActionsStep(pm).run(DataContext(start=day, end=day, trade_dates=(day,)))
    access = Access(pm=pm, processed_version="v1")
    assert len(access.corporate_actions(record_date=day)) == 2
    paths = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=day)
    with pq.ParquetFile(paths.payload_path) as parquet:
        incomplete = parquet.read().slice(0, 1)
    write_parquet_atomic(output_file=paths.payload_path, table=incomplete)
    meta.commit(pm=pm, payload_path=paths.payload_path)
    with pytest.raises(ValueError, match="association is incomplete"):
        access.corporate_actions(record_date=day)
