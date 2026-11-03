# filepath: research/subscription-training/forward_capture.py
"""Record isolated H08 predictions before the registered decision deadline."""

import argparse
from collections.abc import Callable
from datetime import datetime, time
import hashlib
import io
import importlib.metadata
import json
from pathlib import Path
import platform
from zoneinfo import ZoneInfo

import joblib
import pyarrow.parquet as pq
from threadpoolctl import threadpool_limits

import src
from src import logs
from src.access import Access, meta
from src.data_system.builders.feature_tushare_daily_basic import TushareDailyBasicV1Builder
from src.data_system.builders.previous_day import PREOPEN_RULES, build_previous_day_features, qualify_previous_day
from src.training.top_selection import select_top_symbols
from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


def _capture(
    *, trade_date: str, source_root: Path, study_root: Path,
    clock: Callable[[], datetime],
) -> Path:
    started = clock()
    if study_root.resolve().is_relative_to(source_root.resolve()):
        raise ValueError("research output must be outside the source storage root")
    day = DateTimeUtils.require_system_date(trade_date, field_name="trade_date")
    deadline = datetime.combine(datetime.fromisoformat(day).date(), time(9), ZoneInfo("Asia/Shanghai"))
    protocol_bytes = (study_root / "protocol.json").read_bytes()
    protocol = json.loads(protocol_bytes)
    if day not in protocol["dates"]:
        raise ValueError(f"trade_date is outside the registered window: {day}")
    if started.date().isoformat() != day or started > deadline:
        raise ValueError(f"decision capture must occur on {day} before {deadline.isoformat()}")
    runtime = Path(protocol["runtime_source_root"])
    if Path(src.__file__).resolve() != runtime / "src/__init__.py":
        raise ValueError("use the registered frozen runtime via PYTHONPATH")
    manifest_bytes = Path(protocol["source_manifest_path"]).read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != protocol["source_manifest_sha256"]:
        raise ValueError("source manifest digest changed")
    for relative, record in json.loads(manifest_bytes).items():
        if hashlib.sha256((runtime / relative).read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"frozen runtime digest changed: {relative}")
    recorder_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if recorder_digest != protocol["recorder_sha256"]:
        raise ValueError("research recorder digest changed")
    environment = {"python": platform.python_version(),
                   "packages": {name: importlib.metadata.version(name) for name in protocol["environment"]["packages"]}}
    if environment != protocol["environment"]:
        raise ValueError("runtime environment differs from the registered environment")
    for name in ("raw", "staging", "processed", "features", "labels", "experiments"):
        if not (source_root / name).is_dir():
            raise FileNotFoundError(f"source namespace must already exist: {name}")
    attempt = study_root / "captures" / day
    attempt.mkdir(parents=True, exist_ok=False)
    try:
        snapshot = attempt / "inputs"
        snapshot.mkdir()
        source_pm, snapshot_pm = PathManager(source_root), PathManager(snapshot)
        access = Access(pm=source_pm, processed_version="v1")
        previous = access.recent_trade_dates(end_date=day, sessions=2)[0]
        builder = TushareDailyBasicV1Builder()
        history = access.recent_trade_dates(end_date=previous, sessions=builder.lookback_sessions + 1)
        pending = [source_pm.processed_year_object(dataset_name="trade_calendar", version="v1", calendar_year=2026).meta_path]
        pending.extend(source_pm.processed_object(dataset_name=name, version="v1", trade_date=d).meta_path
                       for d in history for name in ("daily_bar", "adj_factor"))
        pending.extend(source_pm.processed_object(dataset_name="daily_basic", version="v1", trade_date=d).meta_path
                       for d in history[-21:-1])
        pending.append(source_pm.feature_object(feature_set="l2_stock_1430", version="v1", trade_date=previous).meta_path)
        pending.extend(source_pm.processed_object(dataset_name=name, version="v1", trade_date=previous).meta_path
                       for name in ("stock_basic", "stock_st", "suspend_d"))
        copied: set[Path] = set()
        received = []
        while pending:
            source_meta = pending.pop()
            if source_meta in copied:
                continue
            record = meta.require(pm=source_pm, meta_path=source_meta)
            if record.upstream is not None:
                pending.append(source_root / str(record.upstream[0]))
            for path in (source_meta, record.payload_path):
                content = path.read_bytes()
                observed_at = clock()
                if observed_at > deadline:
                    raise ValueError(f"input received after decision: {path}")
                relative = path.relative_to(source_root)
                destination = snapshot / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                received.append({"path": str(relative), "sha256": hashlib.sha256(content).hexdigest(),
                                 "size_bytes": len(content), "observer_received_at": observed_at.isoformat()})
            copied.add(source_meta)
        calendar_path = snapshot_pm.processed_year_object(dataset_name="trade_calendar", version="v1", calendar_year=2026).payload_path
        if hashlib.sha256(calendar_path.read_bytes()).hexdigest() != protocol["calendar_2026_sha256"]:
            raise ValueError("captured calendar differs from the registered calendar")
        frozen_access = Access(pm=snapshot_pm, processed_version="v1")
        if frozen_access.recent_trade_dates(end_date=previous, sessions=builder.lookback_sessions + 1) != history:
            raise ValueError("calendar changed while capturing inputs")
        daily = builder.build(access=frozen_access, trade_dates=tuple(history)).to_pandas()
        l2_paths = snapshot_pm.feature_object(feature_set="l2_stock_1430", version="v1", trade_date=previous)
        l2_record = meta.require(pm=snapshot_pm, meta_path=l2_paths.meta_path)
        with pq.ParquetFile(l2_record.payload_path) as source:
            l2 = source.read().to_pandas()
        statuses = {}
        for name in ("stock_basic", "stock_st", "suspend_d"):
            paths = snapshot_pm.processed_object(dataset_name=name, version="v1", trade_date=previous)
            record = meta.require(pm=snapshot_pm, meta_path=paths.meta_path)
            with pq.ParquetFile(record.payload_path) as source:
                statuses[name] = source.read().to_pandas()
            if not statuses[name].trade_date.eq(previous).all():
                raise ValueError(f"qualification snapshot date differs from P: {name}")
        features = build_previous_day_features(l2=l2, daily=daily, trade_date=day, previous_trade_date=previous)
        eligibility = qualify_previous_day(features.symbol.tolist(), listed=statuses["stock_basic"],
                                            st=statuses["stock_st"], suspension=statuses["suspend_d"], target_date=day)
        eligibility["trade_date"] = day
        eligibility["source_date"] = previous
        features = features.loc[eligibility.eligible.to_numpy()].reset_index(drop=True)
        daily.to_parquet(attempt / "daily-features.parquet", index=False)
        eligibility.to_parquet(attempt / "eligibility.parquet", index=False)
        features.to_parquet(attempt / "features.parquet", index=False)
        selections = {}
        model_receipts = {}
        for method, specification in protocol["models"].items():
            model_path = study_root / specification["path"]
            model_bytes = model_path.read_bytes()
            if hashlib.sha256(model_bytes).hexdigest() != specification["sha256"]:
                raise ValueError(f"frozen model digest changed: {method}")
            model_received = clock()
            model = joblib.load(io.BytesIO(model_bytes))
            model_ready = clock()
            with threadpool_limits(limits=1):
                predictions = model.predict(features[specification["columns"]].to_numpy(dtype=float))
            scores = features[["symbol", "trade_date", "decision_ts_utc"]].copy()
            scores["prediction"] = predictions
            scores.to_parquet(attempt / f"predictions-{method}.parquet", index=False)
            positions = select_top_symbols(features.symbol.to_numpy(dtype=str), predictions, top_k=protocol["k"])
            selections[method] = features.symbol.iloc[positions].tolist()
            model_receipts[method] = {"sha256": specification["sha256"], "observer_received_at": model_received.isoformat(),
                                      "model_ready_at": model_ready.isoformat()}
        for method, column in PREOPEN_RULES.items():
            positions = select_top_symbols(features.symbol.to_numpy(dtype=str), features[column].to_numpy(dtype=float),
                                           top_k=protocol["k"])
            selections[method] = features.symbol.iloc[positions].tolist()
        finished = clock()
        if finished > deadline:
            raise ValueError(f"predictions completed after decision: {finished.isoformat()}")
        outputs = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in attempt.glob("*.parquet")}
        receipt = {"trade_date": day, "source_date": previous, "started_at": started.isoformat(),
                   "predictions_ready_at": finished.isoformat(), "deadline": deadline.isoformat(),
                   "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
                   "source_manifest_sha256": protocol["source_manifest_sha256"], "recorder_sha256": recorder_digest,
                   "environment": environment,
                   "observer": "research process reading local committed objects; not provider ingestion time",
                   "inputs": received, "models": model_receipts, "candidate_rows": len(features),
                   "selected": selections, "outputs": outputs, "eligible_for_confirmation": True,
                   "evaluation_labels_read": False, "source_writes": False}
        receipt_path = attempt / "receipt.json"
        FileSystem.write_bytes_atomic(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2).encode())
        if clock() > deadline:
            receipt_path.unlink()
            raise ValueError("receipt publication completed after decision")
        return receipt_path
    except Exception as error:
        failure = {"trade_date": day, "failed_at": clock().isoformat(), "error_type": type(error).__name__,
                   "message": str(error), "eligible_for_confirmation": False}
        FileSystem.write_bytes_atomic(attempt / "failure.json", json.dumps(failure, ensure_ascii=False, indent=2).encode())
        raise


def _main() -> None:
    parser = argparse.ArgumentParser(description="Capture one isolated H08 decision; never fetch or publish market data.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--study-root", required=True, type=Path)
    args = parser.parse_args()
    receipt = _capture(trade_date=args.date, source_root=args.source_root.resolve(strict=True),
                       study_root=args.study_root.resolve(strict=True), clock=lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
    logs.info(f"Research decision captured; receipt={receipt}")


if __name__ == "__main__":
    _main()
