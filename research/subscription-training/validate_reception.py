# filepath: research/subscription-training/validate_reception.py
"""Verify received-prefix minute features under the frozen H03 scenarios."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import shutil
import subprocess
import tarfile
import tempfile
from datetime import date, time
from pathlib import Path
from typing import TypedDict

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src.access import Access
from src.data_system.builders.level2_stock_trade_1m import build_level2_stock_trade_1m
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager


class _SnapshotCheck(TypedDict):
    scenario: str
    symbol: str
    decision_ts_utc: int
    batch_size: int
    received_prefix_rows: int
    window_trade_count: int
    minute_rows: int
    connection_covers_window: bool
    batch_equals_offline: bool


def _minute_features(received_prefix: pa.Table, *, decision_ts: int) -> pa.Table:
    """Rebuild direction within one legal continuous reception segment."""
    ordered = received_prefix.sort_by(
        [(name, "ascending") for name in ("ts_utc", "main_seq", "sub_seq")]
    )
    prices = ordered["price"].to_numpy()
    sides = np.zeros(len(prices), dtype=np.int8)
    sides[1:] = np.sign(prices[1:] - prices[:-1])
    ordered = ordered.set_column(
        ordered.schema.get_field_index("trade_side"),
        "trade_side",
        pa.array(sides, type=pa.int8()),
    )
    minutes = build_level2_stock_trade_1m(ordered, trade_date="2026-08-25")
    return minutes.filter(
        pc.and_(
            pc.greater_equal(minutes["minute_start_ts_utc"], decision_ts - 300_000_000),
            pc.less(minutes["minute_start_ts_utc"], decision_ts),
        )
    )


def _fingerprint(table: pa.Table) -> str:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table.combine_chunks())
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def _negative_controls(source: pa.Table, stamps: dict[str, int]) -> dict[str, bool]:
    fixture = source.slice(0, 3)
    columns = {
        "ts_utc": pa.array(
            [stamps["14:00"], stamps["14:01"], stamps["14:01"] + 1_000_000], pa.int64()
        ),
        "price": pa.array([9.0, 10.0, 9.5]),
        "volume": pa.array([100, 100, 100], pa.int64()),
        "notional": pa.array([900.0, 1000.0, 950.0]),
        "trade_side": pa.array([0, 1, -1], pa.int8()),
        "phase": pa.array([2, 2, 2], pa.int8()),
    }
    for name, values in columns.items():
        fixture = fixture.set_column(fixture.schema.get_field_index(name), name, values)
    observed = fixture.slice(1)
    correct = _minute_features(observed, decision_ts=stamps["14:04"])
    inherited = build_level2_stock_trade_1m(observed, trade_date="2026-08-25")
    assert correct["tick_signed_volume_sum"].to_pylist() == [-100]
    assert inherited["tick_signed_volume_sum"].to_pylist() == [0]

    arrival_order = fixture.take(pa.array([0, 2, 1], pa.int64()))
    arrival_prices = arrival_order["price"].to_numpy()
    wrong_sides = np.zeros(3, dtype=np.int8)
    wrong_sides[1:] = np.sign(np.diff(arrival_prices))
    wrongly_signed = arrival_order.set_column(
        arrival_order.schema.get_field_index("trade_side"),
        "trade_side",
        pa.array(wrong_sides),
    )
    wrong = build_level2_stock_trade_1m(wrongly_signed, trade_date="2026-08-25")
    corrected = _minute_features(arrival_order, decision_ts=stamps["14:04"])
    assert not wrong.equals(corrected)
    assert corrected.equals(_minute_features(fixture, decision_ts=stamps["14:04"]))
    return {
        "inherited_direction_rejected": True,
        "arrival_order_direction_rejected": True,
    }


def _validate_scenarios(
    tables: dict[str, pa.Table], *, evidence: Path
) -> tuple[list[_SnapshotCheck], dict[str, bool]]:
    stamps = {
        label: DateTimeUtils.local_time_to_utc_epoch_us(
            time.fromisoformat(label), date(2026, 8, 25)
        )
        for label in (
            "14:00",
            "14:01",
            "14:04",
            "14:05",
            "14:10",
            "14:12",
            "14:13",
            "14:14",
            "14:20",
            "14:30",
            "14:31",
        )
    }
    decisions = [
        stamps[label] for label in ("14:04", "14:10", "14:14", "14:20", "14:30")
    ]
    checks: list[_SnapshotCheck] = []
    saved_snapshots: list[tuple[pa.Table, str]] = []
    negative_controls = _negative_controls(tables["600000"], stamps)
    for scenario in ("regular", "reordered", "disconnect"):
        for symbol, source in tables.items():
            events = source["ts_utc"].to_numpy()
            delays = np.full(len(events), 100_000, dtype=np.int64)
            if scenario != "regular":
                delays[(np.arange(len(events)) + 1) % 31 == 0] += 7_000_000
            receipts = events + delays
            messages = source.append_column("recv_ts_utc", pa.array(receipts))
            pq.write_table(messages, evidence / f"messages-{scenario}-{symbol}.parquet")
            first_start = stamps["14:01" if symbol == "600000" else "14:05"]
            intervals = [(first_start, stamps["14:31"])]
            if scenario == "disconnect" and symbol == "600000":
                intervals = [
                    (first_start, stamps["14:12"]),
                    (stamps["14:13"], stamps["14:31"]),
                ]
            arrivals = messages.sort_by(
                [
                    (name, "ascending")
                    for name in ("recv_ts_utc", "ts_utc", "main_seq", "sub_seq")
                ]
            )
            receipt_order = arrivals["recv_ts_utc"].to_numpy()
            for batch_size in (1, 17, 257):
                inbox: list[pa.Table] = []
                cursor = 0
                active_start: int | None = None
                for decision in decisions:
                    interval = next(
                        (
                            (start, end)
                            for start, end in intervals
                            if start <= decision < end
                        ),
                        None,
                    )
                    stop = int(np.searchsorted(receipt_order, decision, side="right"))
                    if interval is None:
                        inbox.clear()
                        active_start = None
                    else:
                        start, end = interval
                        if start != active_start:
                            inbox.clear()
                            active_start = start
                        while cursor < stop:
                            count = min(batch_size, stop - cursor)
                            packet = arrivals.slice(cursor, count)
                            admitted = packet.filter(
                                pc.and_(
                                    pc.and_(
                                        pc.greater_equal(packet["recv_ts_utc"], start),
                                        pc.less(packet["recv_ts_utc"], end),
                                    ),
                                    pc.and_(
                                        pc.greater_equal(packet["ts_utc"], start),
                                        pc.less(packet["ts_utc"], decision),
                                    ),
                                )
                            )
                            if admitted.num_rows:
                                inbox.append(admitted)
                            cursor += count
                    cursor = stop
                    prefix = pa.concat_tables(inbox) if inbox else messages.slice(0, 0)
                    actual = _minute_features(prefix, decision_ts=decision)

                    visible = np.zeros(len(events), dtype=bool)
                    if interval is not None:
                        start, end = interval
                        visible = (
                            (events >= start)
                            & (events < decision)
                            & (receipts >= start)
                            & (receipts < end)
                            & (receipts <= decision)
                        )
                    reference_prefix = messages.filter(pa.array(visible))
                    expected = _minute_features(reference_prefix, decision_ts=decision)
                    assert actual.equals(expected), (
                        scenario,
                        symbol,
                        decision,
                        batch_size,
                    )
                    window_visible = visible & (events >= decision - 300_000_000)
                    expected_count = int(np.count_nonzero(window_visible))
                    assert sum(actual["trade_count"].to_pylist()) == expected_count
                    assert sum(actual["volume_sum"].to_pylist()) == int(
                        source["volume"].to_numpy()[window_visible].sum()
                    )
                    assert math.isclose(
                        math.fsum(actual["notional_sum"].to_pylist()),
                        math.fsum(source["notional"].to_numpy()[window_visible]),
                        rel_tol=1e-12,
                        abs_tol=1e-7,
                    )
                    coverage = (
                        interval is not None and interval[0] <= decision - 300_000_000
                    )
                    if (
                        scenario == "disconnect"
                        and symbol == "600000"
                        and decision == stamps["14:14"]
                    ):
                        assert not coverage
                    checks.append(
                        {
                            "scenario": scenario,
                            "symbol": symbol,
                            "decision_ts_utc": decision,
                            "batch_size": batch_size,
                            "received_prefix_rows": prefix.num_rows,
                            "window_trade_count": expected_count,
                            "minute_rows": actual.num_rows,
                            "connection_covers_window": coverage,
                            "batch_equals_offline": True,
                        }
                    )
                    if batch_size == 17:
                        pq.write_table(
                            actual,
                            evidence
                            / f"snapshot-{scenario}-{symbol}-{decision}.parquet",
                        )
                        saved_snapshots.append((actual, _fingerprint(actual)))
                        poisoned_prices = np.where(
                            visible, source["price"].to_numpy(), 1e12
                        )
                        poisoned = messages.set_column(
                            messages.schema.get_field_index("price"),
                            "price",
                            pa.array(poisoned_prices),
                        ).set_column(
                            messages.schema.get_field_index("trade_side"),
                            "trade_side",
                            pa.array(np.full(len(events), 17, dtype=np.int8)),
                        )
                        poisoned_result = _minute_features(
                            poisoned.filter(pa.array(visible)), decision_ts=decision
                        )
                        assert poisoned_result.equals(actual)
    assert all(_fingerprint(table) == original for table, original in saved_snapshots)
    assert len(checks) == 90
    return checks, negative_controls


def run_reception_validation(
    *, source_root: Path, storage_root: Path, evidence_parent: Path
) -> Path:
    """Archive real trade subsets and execute the H03 artificial-reception checks.

    Example:
        evidence = run_reception_validation(
            source_root=Path('/home/wsw/app/dev/trading'),
            storage_root=Path('/home/wsw/app/data'),
            evidence_parent=Path('/home/wsw/app/research-evidence'),
        )
    """
    evidence = Path(
        tempfile.mkdtemp(
            prefix="subscription-reception-2026-09-19-", dir=evidence_parent
        )
    )
    research = source_root / "research/subscription-training"
    shutil.copy2(research / "README.md", evidence / "preregistered-README.md")
    shutil.copy2(research / "validate_reception.py", evidence / "validate_reception.py")
    with tarfile.open(evidence / "source.tar", "w") as archive:
        for path in sorted((source_root / "src").rglob("*.py")):
            archive.add(path, arcname=path.relative_to(source_root))
        for name in ("pyproject.toml", "uv.lock"):
            archive.add(source_root / name, arcname=name)
    pm = PathManager(storage_root)
    access = Access(pm=pm, processed_version="v1")
    full_tables = access.trades(trade_date="2026-08-25", symbols=("600000", "000001"))
    lower = DateTimeUtils.local_time_to_utc_epoch_us(time(14), date(2026, 8, 25))
    upper = DateTimeUtils.local_time_to_utc_epoch_us(time(14, 30), date(2026, 8, 25))
    tables: dict[str, pa.Table] = {}
    origins: list[dict[str, str | int]] = []
    for symbol, full_table in full_tables.items():
        table = full_table.filter(
            pc.and_(
                pc.greater_equal(full_table["ts_utc"], lower),
                pc.less(full_table["ts_utc"], upper),
            )
        )
        assert table.num_rows >= 3
        assert pc.all(pc.equal(table["security_type"], "stock")).as_py()
        tables[symbol] = table
        pq.write_table(table, evidence / f"input-{symbol}.parquet")
        dataset = "sh_trade" if symbol == "600000" else "sz_trade"
        paths = pm.processed_object(
            dataset_name=dataset, version="v1", trade_date="2026-08-25"
        )
        shutil.copy2(paths.meta_path, evidence / f"source-meta-{symbol}.json")
        origins.append(
            {
                "symbol": symbol,
                "source": str(paths.payload_path),
                "source_size_bytes": paths.payload_path.stat().st_size,
                "retained_rows": table.num_rows,
                "retained_table_sha256": _fingerprint(table),
            }
        )
    run = {
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
        ).strip(),
        "packages": {
            name: importlib.metadata.version(name) for name in ("numpy", "pyarrow")
        },
        "origins": origins,
        "reception_timestamps_are_simulated": True,
        "real_time_alignment_verified": False,
        "alpha_verified": False,
    }
    (evidence / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    checks, negative_controls = _validate_scenarios(tables, evidence=evidence)
    (evidence / "checks.json").write_text(
        json.dumps(checks, indent=2), encoding="utf-8"
    )
    (evidence / "summary.json").write_text(
        json.dumps(
            {
                "comparisons": len(checks),
                "all_comparisons_equal": True,
                "independent_count_volume_notional_checks": True,
                "unreceived_and_persisted_direction_poison_checks": True,
                "past_snapshots_unchanged": True,
                "negative_controls": negative_controls,
                "real_time_alignment_verified": False,
                "alpha_verified": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    lines = []
    for path in sorted(evidence.iterdir()):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    (evidence / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
    return evidence
