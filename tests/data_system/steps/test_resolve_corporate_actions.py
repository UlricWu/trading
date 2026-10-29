# filepath: tests/data_system/steps/test_resolve_corporate_actions.py
"""Publication, review lineage and reuse at the corporate-action data boundary."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from tests.fixtures.distributions import verified_distributions

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import TypeAdapter

from src.access import Access, meta
from src.data_system.context import DataContext
from src.data_system.corporate_actions import DividendRecord
from src.data_system.distribution_announcements import AnnouncementText, ReviewExtractionManifest, extract_distribution_review
from src.data_system.steps.resolve_corporate_actions import ResolveCorporateActionsStep
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


@pytest.mark.parametrize("symbol,status,issue", [
    ("000736", "resolved", "settlement_date_missing"),
    ("600803", "resolved", "shareholder_scope_unclear"),
    ("300016", "resolved", "announcement_payment_year_conflict"),
    ("605299", "unresolved", "distribution_amount_conflict"),
    ("002427", "unresolved", "special_beneficiary_requires_review"),
])
def test_real_publications_separate_extraction_gaps_from_business_conflicts(
    tmp_path: Path, symbol: str, status: str, issue: str,
) -> None:
    cases = json.loads((Path(__file__).parents[2] /
        "fixtures/corporate_actions/implementation_publications.json").read_text())
    case = next(case for case in cases if case["symbol"] == symbol)
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    documents = TypeAdapter(tuple[AnnouncementText, ...]).validate_python(case["documents"])
    extracted = extract_distribution_review(records, documents)
    assert extracted.review is None and extracted.reason.startswith(issue)
    day = str(records[0].record_date)
    pm = PathManager(tmp_path)
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=day, payload_file="reviews.json")
    FileSystem.write_bytes_atomic(path, b"[]")
    meta.commit(pm=pm, payload_path=path)
    manifest = ReviewExtractionManifest(inputs=(), issues={records[0].candidate_id: extracted.reason})
    FileSystem.write_bytes_atomic(path.parent / "extraction.json", manifest.model_dump_json().encode())

    ResolveCorporateActionsStep(pm).run(DataContext(start=day, end=day, trade_dates=(day,)))

    result, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=day)
    assert result.status == status
    assert (result.source_record is not None) == (status == "resolved")
    if symbol == "600803":
        assert str(result.source_record.cash_div_tax) == "1.03"
        assert result.source_record.imp_ann_date == date(2025, 7, 16)
    elif symbol == "300016":
        assert result.source_record == records[0]
        assert result.source_record.pay_date == date(2019, 4, 3)
        assert result.source_record.cash_div_tax == Decimal("0.1")
        assert "pdf=2018-04-03:dividend=2019-04-03" in manifest.issues[result.candidate_id]


def test_real_cash_distribution_uses_dividend_then_confirmed_implementation(tmp_path: Path) -> None:
    cases = json.loads((Path(__file__).parents[2] /
        "fixtures/corporate_actions/implementation_publications.json").read_text())
    case = next(case for case in cases if case["symbol"] == "002541")
    records = TypeAdapter(tuple[DividendRecord, ...]).validate_python(case["records"])
    documents = TypeAdapter(tuple[AnnouncementText, ...]).validate_python(case["documents"])
    day = str(records[0].record_date)
    pm = PathManager(tmp_path)
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=source.payload_path, table=pa.Table.from_pylist(case["records"]))
    meta.commit(pm=pm, payload_path=source.payload_path)
    context = DataContext(start=day, end=day, trade_dates=(day,))
    step = ResolveCorporateActionsStep(pm)
    access = Access(pm=pm, processed_version="v1")

    step.run(context)
    original, = access.corporate_actions(record_date=day)
    assert original.status == "resolved"
    assert original.reason == "ordinary_dividend_source"
    assert original.source_record == records[0]
    assert original.source_record.cash_div_tax == Decimal("0.259999")
    assert original.review is None

    extracted = extract_distribution_review(records, documents)
    assert extracted.review is not None, extracted.reason
    path = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=day, payload_file="reviews.json")
    FileSystem.write_bytes_atomic(path, json.dumps([extracted.review.model_dump(mode="json")]).encode())
    meta.commit(pm=pm, payload_path=path)
    step.run(context)
    corrected, = access.corporate_actions(record_date=day)
    assert corrected.status == "resolved"
    assert corrected.source_record is None
    assert corrected.review == extracted.review
    terms, = corrected.review.terms
    assert terms.cash_per_share == Decimal("0.2599999")
    assert terms.reference_cash_per_share == Decimal("0.259999")
    assert terms.shares_per_share == terms.reference_shares_per_share == 0
    assert terms.published_date == date(2023, 6, 7)
    assert corrected.review.record_date == date(2023, 6, 13)
    assert corrected.review.ex_date == corrected.review.pay_date == date(2023, 6, 14)
    assert corrected.review.div_listdate is None
    with pq.ParquetFile(source.payload_path) as parquet:
        assert parquet.read().to_pylist() == case["records"]


@pytest.fixture(params=[False, True], ids=["explicit_cash", "null_cash_stock"])
def ordinary_partition(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[PathManager, DataContext, DividendRecord]:
    row = DividendRecord.model_validate_json(
        (Path(__file__).parents[2] / "fixtures/corporate_actions/ordinary_dividend.json").read_bytes())
    if request.param:
        row = row.model_copy(update={"cash_div_tax": None, "pay_date": None})
    pm = PathManager(tmp_path)
    day = row.record_date.isoformat()
    paths = pm.processed_object(dataset_name="dividend", version="v1", trade_date=day)
    write_parquet_atomic(output_file=paths.payload_path, table=pa.Table.from_pylist([row.model_dump(mode="json")]))
    meta.commit(pm=pm, payload_path=paths.payload_path)
    return pm, DataContext(start=day, end=day, trade_dates=(day,)), row


def test_ordinary_partition_needs_no_announcement_objects_and_reuses_complete_result(
    ordinary_partition: tuple[PathManager, DataContext, DividendRecord],
) -> None:
    pm, context, row = ordinary_partition
    step = ResolveCorporateActionsStep(pm)
    step.run(context)
    result, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert result.status == "resolved"
    assert result.source_record == row
    assert result.review is None
    path = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start).payload_path
    with pq.ParquetFile(path) as parquet:
        inputs = json.loads(parquet.schema_arrow.metadata[b"corporate_action_inputs"])
    assert set(inputs) == {f"processed/dividend/v1/trade_date={context.start}/meta.json"}
    before = path.stat().st_mtime_ns
    step.run(context)
    assert path.stat().st_mtime_ns == before


def test_equivalent_observations_preserve_source_rows_and_publish_one_event(
    ordinary_partition: tuple[PathManager, DataContext, DividendRecord],
) -> None:
    pm, context, row = ordinary_partition
    source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    earlier = row.model_copy(update={"imp_ann_date": date(2026, 7, 15)})
    observations = (earlier, row, row.model_copy(update={"cash_div_tax": None}))
    write_parquet_atomic(output_file=source.payload_path,
        table=pa.Table.from_pylist([item.model_dump(mode="json") for item in observations]))
    meta.commit(pm=pm, payload_path=source.payload_path)
    source_bytes = source.payload_path.read_bytes()
    source_mtime = source.payload_path.stat().st_mtime_ns
    step = ResolveCorporateActionsStep(pm)
    step.run(context)
    result, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert result.status == "resolved"
    assert result.source_record == row
    assert result.review is None
    output = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start)
    output_bytes = output.payload_path.read_bytes()
    output_mtime = output.payload_path.stat().st_mtime_ns
    step.run(context)
    assert source.payload_path.read_bytes() == source_bytes
    assert source.payload_path.stat().st_mtime_ns == source_mtime
    assert output.payload_path.read_bytes() == output_bytes
    assert output.payload_path.stat().st_mtime_ns == output_mtime


@pytest.mark.parametrize(("issue", "status"), [
    ("announcements_not_collected:cninfo", "resolved"),
    ("unsupported_announcement_exchange", "resolved"),
    ("pdf_text_unreadable:cninfo:123:empty_text", "resolved"),
    ("missing_implementation", "resolved"),
    ("implementation_event_not_matched", "resolved"),
    ("ordinary_cash_entitlement_unconfirmed:123", "unresolved"),
    ("implementation_stage_unconfirmed:123", "unresolved"),
    ("ex_reference_not_confirmed:123", "resolved"),
    ("settlement_date_missing:123", "resolved"),
    ("zero_cash_not_confirmed:123", "resolved"),
    ("special_beneficiary_requires_review:123", "unresolved"),
    ("separate_revision_requires_review:123", "unresolved"),
    ("distribution_amount_ambiguous:123", "resolved"),
    ("settlement_date_ambiguous:123", "resolved"),
    ("nonstandard_reporting_period", "resolved"),
    ("distribution_amount_conflict:123", "unresolved"),
    ("unsupported_announcement_timeline:123", "unresolved"),
    ("phase_date_revision_requires_review", "unresolved"),
    ("previous_review_requires_confirmation:missing_implementation", "unresolved"),
])
@pytest.mark.parametrize("duplicate", [False, True])
def test_optional_extraction_failure_is_distinct_from_known_conflicting_evidence(
    ordinary_partition: tuple[PathManager, DataContext, DividendRecord], issue: str, status: str, duplicate: bool,
) -> None:
    pm, context, row = ordinary_partition
    if duplicate:
        source = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
        write_parquet_atomic(output_file=source.payload_path,
            table=pa.Table.from_pylist([row.model_dump(mode="json")] * 2))
        meta.commit(pm=pm, payload_path=source.payload_path)
    path = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    FileSystem.write_bytes_atomic(path, b"[]")
    manifest = ReviewExtractionManifest(inputs=(), issues={row.candidate_id: issue})
    FileSystem.write_bytes_atomic(path.parent / "extraction.json", manifest.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=path)
    ResolveCorporateActionsStep(pm).run(context)
    result, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert result.status == status
    assert result.reason == ("ordinary_dividend_source" if status == "resolved" else issue)
    assert (result.source_record is not None) == (status == "resolved")


def test_same_inputs_do_not_preserve_an_obsolete_unreviewed_disposition(
    ordinary_partition: tuple[PathManager, DataContext, DividendRecord],
) -> None:
    pm, context, _ = ordinary_partition
    step = ResolveCorporateActionsStep(pm)
    step.run(context)
    paths = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start)
    with pq.ParquetFile(paths.payload_path) as parquet:
        table = parquet.read()
    rows = table.to_pylist()
    prior = json.loads(rows[0]["resolution"])
    prior.update(status="unresolved", reason="unverified_beneficiary_and_reference")
    prior.pop("source_record")
    rows[0]["resolution"] = json.dumps(prior)
    write_parquet_atomic(output_file=paths.payload_path, table=pa.Table.from_pylist(rows, schema=table.schema))
    meta.commit(pm=pm, payload_path=paths.payload_path)
    step.run(context)
    result, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert result.status == "resolved"
    assert result.source_record is not None
    assert not (paths.payload_path.parent / "history").exists()


@pytest.fixture
def source(tmp_path: Path) -> tuple[PathManager, DataContext, dict[str, object]]:
    case = verified_distributions()[1]
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
    before = paths.payload_path.read_bytes()
    changed = deepcopy(case["review"])
    changed["candidate_id"] = "unrelated-candidate"
    FileSystem.write_bytes_atomic(review_path, json.dumps([changed]).encode())
    meta.commit(pm=pm, payload_path=review_path)
    with pytest.raises(ValueError, match="no matching implemented source"):
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


def test_explicit_correction_republishes_without_a_content_binding(
    source: tuple[PathManager, DataContext, dict[str, object]],
) -> None:
    pm, context, case = source
    review_path = pm.raw_payload(broker="announcements", source_name="corporate_action_reviews",
        trade_date=context.start, payload_file="reviews.json")
    payload = json.dumps([case["review"]]).encode()
    FileSystem.write_bytes_atomic(review_path, payload)
    manifest = ReviewExtractionManifest(inputs=(), issues={})
    FileSystem.write_bytes_atomic(review_path.parent / "extraction.json", manifest.model_dump_json().encode())
    meta.commit(pm=pm, payload_path=review_path)
    step = ResolveCorporateActionsStep(pm)
    step.run(context)
    output = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start).payload_path
    previous = output.read_bytes()
    changed = payload.replace(b'"0.3158"', b'"0.3159"')
    assert changed != payload and len(changed) == len(payload)
    FileSystem.write_bytes_atomic(review_path, changed)

    step.run(context)
    result, = Access(pm=pm, processed_version="v1").corporate_actions(record_date=context.start)
    assert str(result.review.terms[0].cash_per_share) == "0.3159"
    assert output.read_bytes() != previous


def test_access_requires_all_candidates_of_an_associated_event(tmp_path: Path) -> None:
    cases = verified_distributions()
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


def test_changed_source_can_rebuild_owned_partition_without_accepting_stale_output(
    source: tuple[PathManager, DataContext, dict[str, object]],
) -> None:
    pm, context, case = source
    step = ResolveCorporateActionsStep(pm)
    step.run(context)
    output = pm.processed_object(dataset_name="corporate_actions", version="v1", trade_date=context.start)
    original = output.payload_path.read_bytes()
    paths = pm.processed_object(dataset_name="dividend", version="v1", trade_date=context.start)
    rows = deepcopy(case['records'])
    rows.append({**rows[0], 'symbol': '000001', 'ts_code': '000001.SZ'})
    write_parquet_atomic(output_file=paths.payload_path, table=pa.Table.from_pylist(rows))
    meta.commit(pm=pm, payload_path=paths.payload_path)
    access = Access(pm=pm, processed_version='v1')
    with pytest.raises(RuntimeError, match='upstream payload size changed'):
        access.corporate_actions(record_date=context.start)
    step.run(context)
    resolutions = access.corporate_actions(record_date=context.start)
    assert len(resolutions) == 2
    assert {row.symbol for row in resolutions} == {rows[0]['symbol'], '000001'}
    assert output.payload_path.read_bytes() != original
