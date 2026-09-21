# filepath: research/subscription-training/validate_training.py
"""Run the frozen research in README.md without writing authoritative storage."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from datetime import time as wall_time
from pathlib import Path
from typing import TypedDict

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from threadpoolctl import threadpool_limits

from src.access import Access, meta
from src.config.model_config import MissingConfig, PreprocessingConfig
from src.data_system.builders.stock_1430 import (
    STOCK_1430_FEATURE_SCHEMA,
    STOCK_1430_KEY_SCHEMA,
    require_stock_1430_feature_keys,
)
from src.data_system.builders.stock_1430_daily_l2 import STOCK_1430_DAILY_SOURCE_COLUMNS
from src.training.engines.dataset import build_daily_training_dataset
from src.training.engines.preprocessing import FittedPreprocessor
from src.utils import table_ops
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import ObjectPaths, PathManager


@dataclass(frozen=True)
class _FittedCandidate:
    estimator: Ridge | HistGradientBoostingRegressor
    preprocessor: FittedPreprocessor


class _InputFile(TypedDict):
    path: str
    size_bytes: int
    sha256: str


def _write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _timestamp(session: str, hour: int, minute: int = 0) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(
        wall_time(hour, minute), date.fromisoformat(session)
    )


def _snapshot_object(pm: PathManager, paths: ObjectPaths, snapshot_root: Path) -> None:
    record = meta.require(
        pm=pm, meta_path=paths.meta_path, expected_payload_path=paths.payload_path
    )
    source_paths = [paths.meta_path, record.payload_path]
    if record.upstream is not None:
        upstream_meta = pm.storage_root / record.upstream[0]
        upstream_record = meta.require(pm=pm, meta_path=upstream_meta)
        source_paths.extend((upstream_meta, upstream_record.payload_path))
    for source_path in source_paths:
        destination = snapshot_root / source_path.relative_to(pm.storage_root)
        if destination.exists():
            if _sha256(source_path) != _sha256(destination):
                raise RuntimeError(f"input changed during snapshot: {source_path}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        if _sha256(source_path) != _sha256(destination):
            raise RuntimeError(f"input changed during copy: {source_path}")


def _read_object(pm: PathManager, paths: ObjectPaths) -> pd.DataFrame:
    record = meta.require(
        pm=pm, meta_path=paths.meta_path, expected_payload_path=paths.payload_path
    )
    with pq.ParquetFile(record.payload_path) as parquet:
        return parquet.read().to_pandas()


def _load_panel(pm: PathManager, sessions: Sequence[str]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    daily_columns = list(STOCK_1430_DAILY_SOURCE_COLUMNS)
    l2_columns = STOCK_1430_FEATURE_SCHEMA.names[3:]
    for position in range(1, len(sessions) - 1):
        previous_day, target_day, exit_day = sessions[position - 1 : position + 2]
        history = _read_object(
            pm,
            pm.feature_object(
                feature_set="l2_stock_1430", version="v1", trade_date=previous_day
            ),
        )
        daily = _read_object(
            pm,
            pm.feature_object(
                feature_set="tushare_daily_basic", version="v1", trade_date=previous_day
            ),
        )
        labels = _read_object(
            pm,
            pm.label_object(
                label_set="l2_stock_1430_t1_vwap_rank",
                version="v1",
                trade_date=target_day,
            ),
        )
        for frame, expected_day in [
            (history, previous_day),
            (daily, previous_day),
            (labels, target_day),
        ]:
            table_ops.require_unique(frame, ("symbol",), who="research input")
            if not frame["trade_date"].eq(expected_day).all():
                raise ValueError(f"partition date mismatch: {expected_day}")
        if not history["decision_ts_utc"].eq(_timestamp(previous_day, 14, 30)).all():
            raise ValueError("history decision timestamp mismatch")
        if not labels["decision_ts_utc"].eq(_timestamp(target_day, 14, 30)).all():
            raise ValueError("label decision timestamp mismatch")
        aligned_daily = daily.set_index("symbol").reindex(history["symbol"])
        aligned_labels = labels.set_index("symbol").reindex(history["symbol"])
        features = history.loc[:, ["symbol", *l2_columns]].rename(
            columns={name: f"lag_{name}" for name in l2_columns}
        )
        daily_ranks = aligned_daily[daily_columns].rank(method="average", pct=True)
        for column in daily_columns:
            features[f"lag_{column}_rank"] = daily_ranks[column].to_numpy()
        features["trade_date"] = target_day
        features["source_date"] = previous_day
        features["feature_ready_ts"] = _timestamp(previous_day, 22)
        features["allocation_ts"] = _timestamp(target_day, 9)
        features["label_ready_ts"] = _timestamp(exit_day, 22)
        features["y_rank_return"] = aligned_labels["y_rank_return"].to_numpy()
        features["baseline_amount"] = aligned_daily["f_d_log_amount"].to_numpy()
        features["baseline_momentum"] = aligned_daily[
            "f_d_close_return_5d_asof_tminus1"
        ].to_numpy()
        features["baseline_reversal"] = -features["baseline_momentum"]
        parts.append(features)
    panel = pd.concat(parts, ignore_index=True)
    feature_columns = [f"lag_{name}" for name in l2_columns] + [
        f"lag_{name}_rank" for name in daily_columns
    ]
    if np.isinf(panel[feature_columns].to_numpy(dtype=float)).any():
        raise ValueError("feature infinity")
    valid_labels = panel["y_rank_return"].dropna()
    if not valid_labels.between(0, 1, inclusive="right").all():
        raise ValueError("label outside original percentile range")
    return panel


def _fit_candidate(train: pd.DataFrame, candidate: str, seed: int) -> _FittedCandidate:
    columns = (
        [f"lag_{name}" for name in STOCK_1430_FEATURE_SCHEMA.names[3:]]
        if candidate.endswith("_all")
        else []
    ) + [f"lag_{name}_rank" for name in STOCK_1430_DAILY_SOURCE_COLUMNS]
    labeled = train.loc[train["y_rank_return"].notna()]
    preprocessor = FittedPreprocessor.fit(
        train_X=labeled[columns],
        config=PreprocessingConfig(
            missing=MissingConfig(method="constant", fill_value=0.5)
        ),
    )
    keep, transformed = preprocessor.transform(labeled[columns].to_numpy(dtype=float))
    assert keep.all()
    day_counts = labeled.groupby("trade_date")["trade_date"].transform("size")
    weights = 1 / day_counts.to_numpy(dtype=float)
    weights /= weights.mean()
    if candidate.startswith("ridge_"):
        estimator = Ridge(alpha=100, solver="cholesky")
    else:
        estimator = HistGradientBoostingRegressor(
            max_iter=100,
            max_leaf_nodes=7,
            max_depth=3,
            min_samples_leaf=500,
            learning_rate=0.05,
            l2_regularization=10,
            max_bins=63,
            early_stopping=False,
            random_state=seed,
        )
    estimator.fit(
        transformed, labeled["y_rank_return"].to_numpy(), sample_weight=weights
    )
    return _FittedCandidate(estimator, preprocessor)


def _predict(fitted: _FittedCandidate, frame: pd.DataFrame) -> np.ndarray:
    keep, transformed = fitted.preprocessor.transform(
        frame.loc[:, list(fitted.preprocessor.feature_names)].to_numpy(dtype=float)
    )
    assert keep.all()
    return fitted.estimator.predict(transformed)


def _selected_symbols(frame: pd.DataFrame, scores: np.ndarray, count: int) -> list[str]:
    ranked = frame.loc[:, ["symbol"]].assign(score=scores)
    return (
        ranked.sort_values(
            ["score", "symbol"], ascending=[False, True], na_position="last"
        )["symbol"]
        .iloc[:count]
        .tolist()
    )


def _metric_row(
    frame: pd.DataFrame, scores: np.ndarray, *, count: int, method: str
) -> dict[str, object]:
    selected = _selected_symbols(frame, scores, count)
    labels = frame.set_index("symbol")["y_rank_return"].reindex(selected)
    finite = frame["y_rank_return"].notna().to_numpy() & np.isfinite(scores)
    predicted = pd.Series(scores[finite])
    actual = pd.Series(frame["y_rank_return"].to_numpy()[finite])
    correlation = predicted.corr(actual, method="spearman")
    return {
        "trade_date": frame["trade_date"].iloc[0],
        "method": method,
        "pool_size": len(frame),
        "selected_count": len(selected),
        "label_count": int(labels.notna().sum()),
        "top_rank_observed": float(labels.mean()) if labels.notna().any() else None,
        "top_rank_lower": float(labels.fillna(0).mean()),
        "top_rank_upper": float(labels.fillna(1).mean()),
        "rank_ic": float(correlation) if pd.notna(correlation) else None,
        "rank_mse": float(np.mean((scores[finite] - actual.to_numpy()) ** 2))
        if method.startswith(("ridge_", "hist_"))
        else None,
        "random_rank_expectation": float(frame["y_rank_return"].mean()),
        "selected_symbols": selected,
    }


def _walk_forward(
    panel: pd.DataFrame,
    *,
    evaluation_dates: Sequence[str],
    candidates: Sequence[str],
    rules: Sequence[str],
    output_dir: Path,
    seed: int,
) -> pd.DataFrame:
    output_dir.mkdir()
    training_days = panel.groupby("trade_date", sort=True)["label_ready_ts"].max()
    metrics: list[dict[str, object]] = []
    schedule: list[dict[str, object]] = []
    fitted_models: dict[str, _FittedCandidate] = {}
    fitted_month = ""
    model_ready_ts = 0
    for target_day in evaluation_dates:
        fit_start = _timestamp(target_day, 8)
        training_dates = _eligible_training_dates(
            training_days, fit_start=fit_start, count=60
        )
        frame = panel.loc[panel["trade_date"].eq(target_day)]
        if target_day[:7] != fitted_month:
            train = panel.loc[panel["trade_date"].isin(training_dates)]
            assert train["label_ready_ts"].lt(fit_start).all()
            assert train["feature_ready_ts"].le(train["allocation_ts"]).all()
            for candidate in candidates:
                started = time.perf_counter()
                fitted_models[candidate] = _fit_candidate(train, candidate, seed)
                elapsed = time.perf_counter() - started
                fitted = fitted_models[candidate]
                model_path = output_dir / f"{target_day}-{candidate}.joblib"
                joblib.dump((fitted.estimator, fitted.preprocessor), model_path)
                schedule.append(
                    {
                        "eval_start": target_day,
                        "candidate": candidate,
                        "training_dates": training_dates,
                        "train_rows": len(train),
                        "labeled_rows": int(train["y_rank_return"].notna().sum()),
                        "max_label_ready_ts": int(train["label_ready_ts"].max()),
                        "scenario_fit_start_ts": fit_start,
                        "scenario_model_ready_ts": _timestamp(target_day, 9),
                        "measured_fit_seconds": elapsed,
                        "model_sha256": _sha256(model_path),
                        "feature_names": list(fitted.preprocessor.feature_names),
                        "effective_params": fitted.estimator.get_params(),
                    }
                )
                if elapsed >= 3600:
                    raise RuntimeError("model missed assumed one-hour readiness budget")
                print(
                    f"fit {candidate} {target_day}; days=60; seconds={elapsed:.2f}",
                    flush=True,
                )
            fitted_month = target_day[:7]
            model_ready_ts = _timestamp(target_day, 9)
        _require_model_ready(
            ready_ts=model_ready_ts, decision_ts=_timestamp(target_day, 9)
        )
        prediction_frame = frame.loc[
            :, ["symbol", "trade_date", "y_rank_return"]
        ].copy()
        for candidate in candidates:
            scores = _predict(fitted_models[candidate], frame)
            prediction_frame[candidate] = scores
            metrics.append(_metric_row(frame, scores, count=50, method=candidate))
        for rule in rules:
            scores = frame[rule].to_numpy(dtype=float)
            metrics.append(_metric_row(frame, scores, count=50, method=rule))
        prediction_frame.to_parquet(
            output_dir / f"predictions-{target_day}.parquet", index=False
        )
    _write_json(output_dir / "fit-schedule.json", schedule)
    _write_json(output_dir / "daily-metrics.json", metrics)
    return pd.DataFrame(metrics)


def _eligible_training_dates(
    ready_times: pd.Series, *, fit_start: int, count: int
) -> list[str]:
    legal_days = ready_times.index[ready_times < fit_start].tolist()
    if len(legal_days) < count:
        raise ValueError(f"insufficient mature history before {fit_start}")
    return legal_days[-count:]


def _require_model_ready(*, ready_ts: int, decision_ts: int) -> None:
    if ready_ts > decision_ts:
        raise ValueError("model is not ready at decision")


def _observation_ready(
    *,
    start: int,
    end: int,
    effective: int,
    cutoff: int,
    initial_state_known: bool,
    gaps: Sequence[tuple[int, int]] = (),
) -> bool:
    return (
        initial_state_known
        and effective <= start
        and end <= cutoff
        and not any(gap_start < end and gap_end > start for gap_start, gap_end in gaps)
    )


def _visible_events(
    events: pd.DataFrame, subscriptions: pd.DataFrame, *, cutoff: int
) -> pd.DataFrame:
    joined = events.merge(subscriptions, on=["symbol", "topic"], how="inner")
    # A subscription interval owns receipts; event time alone cannot show receipt.
    visible = joined.loc[
        joined["recv_ts"].ge(joined["effective_ts"])
        & joined["recv_ts"].lt(joined["end_ts"])
        & joined["recv_ts"].le(cutoff)
        & joined["event_ts"].le(cutoff)
    ]
    return (
        visible.loc[:, events.columns].sort_values("receipt_id").reset_index(drop=True)
    )


def _boundary_checks() -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    features = pd.DataFrame(
        {
            "symbol": ["600000", "600001"],
            "trade_date": ["2026-07-20"] * 2,
            "decision_ts_utc": [_timestamp("2026-07-20", 14, 30)] * 2,
            "factor": [1.0, 2.0],
        }
    )
    labels = features.drop(columns="factor").assign(y_rank_return=[0.4, 0.6])
    labels["decision_ts_utc"] += 60_000_000
    existing_X, _ = build_daily_training_dataset(
        feature_frame=features,
        label_frame=labels,
        feature_columns=("factor",),
        label_column="y_rank_return",
    )
    assert len(existing_X) == 2
    findings.append(
        {
            "case": "existing_daily_api_accepts_mismatched_timestamp",
            "observed": True,
            "meaning": "daily API is insufficient for intraday keys; not a daily-contract defect",
        }
    )
    keys = ["symbol", "trade_date", "decision_ts_utc"]
    assert not features[keys].equals(labels[keys])
    try:
        require_stock_1430_feature_keys(
            pa.Table.from_pandas(
                labels[keys], preserve_index=False, schema=STOCK_1430_KEY_SCHEMA
            ),
            trade_date="2026-07-20",
            decision_ts_utc=_timestamp("2026-07-20", 14, 30),
        )
    except ValueError as error:
        assert "decision_ts_utc" in str(error)
        findings.append(
            {"case": "intraday_key_boundary_rejects_timestamp_mismatch", "passed": True}
        )
    else:
        raise AssertionError("intraday timestamp mismatch accepted")
    subscriptions = pd.DataFrame(
        {
            "symbol": ["A"] * 3,
            "topic": ["trans", "order", "market"],
            "effective_ts": [10] * 3,
            "end_ts": [30] * 3,
        }
    )
    events = pd.DataFrame(
        {
            "receipt_id": [1, 2, 3, 4, 5, 6],
            "symbol": ["A", "A", "B", "A", "A", "A"],
            "topic": ["trans"] * 6,
            "event_ts": [5, 15, 15, 16, 30, 18],
            "recv_ts": [9, 16, 16, 25, 30, 18],
            "price": [100, 101, 999, 103, 104, 102],
        }
    )
    observed = _visible_events(events, subscriptions, cutoff=20)
    assert observed["receipt_id"].tolist() == [2, 6]
    poisoned = events.copy()
    poisoned.loc[~poisoned["receipt_id"].isin([2, 6]), "price"] = 1e9
    pd.testing.assert_frame_equal(
        observed, _visible_events(poisoned, subscriptions, cutoff=20)
    )
    findings.append(
        {"case": "unsubscribed_pre_ack_late_and_future_poison", "passed": True}
    )
    assert len(subscriptions[["symbol", "topic"]].drop_duplicates()) == 3
    findings.append({"case": "one_stock_three_topics_cost_three", "passed": True})
    train_cutoff = 100
    maturity = np.array([70, 80, 101])
    label_ready = np.array([90, 105, 110])
    ready_times = pd.Series(
        np.maximum(maturity, label_ready), index=["day1", "day2", "day3"]
    )
    assert _eligible_training_dates(ready_times, fit_start=train_cutoff, count=1) == [
        "day1"
    ]
    try:
        _require_model_ready(ready_ts=109, decision_ts=105)
    except ValueError as error:
        assert "not ready" in str(error)
    else:
        raise AssertionError("late model accepted")
    _require_model_ready(ready_ts=105, decision_ts=105)
    findings.append({"case": "label_readiness_and_model_readiness", "passed": True})
    full_tick_sides = np.sign(
        events.loc[events["symbol"].eq("A"), "price"].diff().fillna(0)
    )
    received_tick_sides = np.sign(observed["price"].diff().fillna(0))
    assert full_tick_sides.iloc[1] == 1 and received_tick_sides.iloc[0] == 0
    findings.append(
        {
            "case": "tick_state_must_restart_at_first_received_event",
            "passed": True,
            "limitation": "orderbook reconstruction and full-window readiness not implemented",
        }
    )
    assert not _observation_ready(
        start=0, end=60, effective=40, cutoff=60, initial_state_known=True
    )
    assert not _observation_ready(
        start=0, end=60, effective=0, cutoff=60, initial_state_known=False
    )
    assert not _observation_ready(
        start=0,
        end=60,
        effective=0,
        cutoff=60,
        initial_state_known=True,
        gaps=((20, 21),),
    )
    assert _observation_ready(
        start=0, end=60, effective=0, cutoff=60, initial_state_known=True
    )
    findings.append(
        {
            "case": "short_or_interrupted_window_and_unknown_state_ineligible",
            "passed": True,
            "limitation": "eligibility predicate only; no real feed or orderbook reconstruction",
        }
    )
    top_before = _selected_symbols(features, np.array([0.8, 0.7]), 1)
    masked_labels = features.assign(y_rank_return=[np.nan, 1.0])
    assert top_before == _selected_symbols(masked_labels, np.array([0.8, 0.7]), 1)
    findings.append(
        {"case": "future_label_missingness_does_not_select_pool", "passed": True}
    )
    preprocessor = FittedPreprocessor.fit(
        train_X=pd.DataFrame({"factor": [1.0, 3.0, np.nan]}),
        config=PreprocessingConfig(missing=MissingConfig(method="mean")),
    )
    _, transformed = preprocessor.transform(np.array([[np.nan], [999.0]]))
    assert transformed[0, 0] == 2.0 and preprocessor.fill_values == (2.0,)
    findings.append({"case": "train_only_preprocessing_state", "passed": True})
    return findings


def run_research(
    *, source_root: Path, storage_root: Path, evidence_parent: Path
) -> Path:
    """Snapshot inputs, check boundaries, select candidates, and evaluate once.

    Example:
        evidence = run_research(
            source_root=Path('/home/wsw/app/dev/trading'),
            storage_root=Path('/home/wsw/app/data'),
            evidence_parent=Path('/home/wsw/app/research-evidence'),
        )
        print(evidence / 'final-summary.json')
    """
    evidence = Path(
        tempfile.mkdtemp(
            prefix="subscription-training-2026-09-19-", dir=evidence_parent
        )
    )
    print(f"EVIDENCE={evidence}", flush=True)
    topic = source_root / "research/subscription-training"
    shutil.copy2(topic / "README.md", evidence / "preregistered-README.md")
    shutil.copy2(topic / "validate_training.py", evidence / "validate_training.py")
    source_paths = [
        *sorted((source_root / "src").rglob("*.py")),
        *sorted((source_root / "tests").rglob("*.py")),
        source_root / "pyproject.toml",
        source_root / "uv.lock",
        source_root / "src/config/base.yml",
    ]
    with tarfile.open(evidence / "source.tar", "w") as archive:
        for source_path in source_paths:
            archive.add(source_path, arcname=str(source_path.relative_to(source_root)))
    seed = 20260919
    _write_json(
        evidence / "run.json",
        {
            "base_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
            ).strip(),
            "worktree_status_before": subprocess.check_output(
                ["git", "status", "--short"], cwd=source_root, text=True
            ),
            "script_sha256": _sha256(evidence / "validate_training.py"),
            "preregistration_sha256": _sha256(evidence / "preregistered-README.md"),
            "source_archive_sha256": _sha256(evidence / "source.tar"),
            "seed": seed,
            "source_storage_root": str(storage_root),
            "runtime_versions": {
                name: importlib.metadata.version(name)
                for name in [
                    "numpy",
                    "pandas",
                    "pyarrow",
                    "scikit-learn",
                    "scipy",
                    "joblib",
                ]
            },
            "readiness": "scenario 22:00, not observed historical timestamps",
            "retention": "until user adoption/rejection decision and review complete",
        },
    )
    checks = _boundary_checks()
    _write_json(evidence / "boundary-checks.json", checks)
    original_pm = PathManager(storage_root)
    original_access = Access(pm=original_pm, processed_version="v1")
    sessions = original_access.trade_dates(
        start_date="2025-11-26", end_date="2026-08-26"
    )
    snapshot_root = evidence / "inputs"
    snapshot_root.mkdir()
    for year in (2025, 2026):
        _snapshot_object(
            original_pm,
            original_pm.processed_year_object(
                dataset_name="trade_calendar", version="v1", calendar_year=year
            ),
            snapshot_root,
        )
    for position in range(1, len(sessions) - 1):
        previous_day, target_day = sessions[position - 1 : position + 1]
        for feature_set in ("l2_stock_1430", "tushare_daily_basic"):
            _snapshot_object(
                original_pm,
                original_pm.feature_object(
                    feature_set=feature_set, version="v1", trade_date=previous_day
                ),
                snapshot_root,
            )
        _snapshot_object(
            original_pm,
            original_pm.label_object(
                label_set="l2_stock_1430_t1_vwap_rank",
                version="v1",
                trade_date=target_day,
            ),
            snapshot_root,
        )
    manifest: list[_InputFile] = [
        {
            "path": str(path.relative_to(snapshot_root)),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(snapshot_root.rglob("*"))
        if path.is_file()
    ]
    _write_json(evidence / "input-manifest.json", manifest)
    snapshot_pm = PathManager(snapshot_root)
    panel = _load_panel(snapshot_pm, sessions)
    panel.to_parquet(evidence / "panel.parquet", index=False)
    _write_json(
        evidence / "panel-summary.json",
        {
            "sessions": len(sessions) - 2,
            "rows": len(panel),
            "start": str(panel["trade_date"].min()),
            "end": str(panel["trade_date"].max()),
            "labeled_rows": int(panel["y_rank_return"].notna().sum()),
            "all_inputs": len(manifest),
            "input_bytes": sum(item["size_bytes"] for item in manifest),
            "panel_sha256": _sha256(evidence / "panel.parquet"),
        },
    )
    legal_dates = panel.groupby("trade_date", sort=True)["label_ready_ts"].max()
    selection_dates = [
        day
        for day in legal_dates.index
        if day <= "2026-04-29" and int((legal_dates < _timestamp(day, 8)).sum()) >= 60
    ]
    final_dates = [
        day for day in legal_dates.index if "2026-05-06" <= day <= "2026-08-25"
    ]
    candidates = ("ridge_daily", "ridge_all", "hist_daily", "hist_all")
    rules = ("baseline_amount", "baseline_momentum", "baseline_reversal")
    with threadpool_limits(limits=1):
        selected_metrics = _walk_forward(
            panel,
            evaluation_dates=selection_dates,
            candidates=candidates,
            rules=rules,
            output_dir=evidence / "selection",
            seed=seed,
        )
        averages = selected_metrics.groupby("method")["top_rank_lower"].mean()
        selected_candidate = min(candidates, key=lambda name: (-averages[name], name))
        selected_rule = min(rules, key=lambda name: (-averages[name], name))
        _write_json(
            evidence / "selection-frozen.json",
            {
                "candidate": selected_candidate,
                "rule": selected_rule,
                "selection_dates": selection_dates,
                "final_dates": final_dates,
                "selection_mean_top_rank_lower": averages.to_dict(),
                "frozen_before_final_predictions": True,
            },
        )
        print(
            f"FROZEN candidate={selected_candidate}; comparator={selected_rule}",
            flush=True,
        )
        final_metrics = _walk_forward(
            panel,
            evaluation_dates=final_dates,
            candidates=(selected_candidate,),
            rules=(selected_rule,),
            output_dir=evidence / "final",
            seed=seed,
        )
    candidate_rows = final_metrics.loc[
        final_metrics["method"].eq(selected_candidate)
    ].set_index("trade_date")
    rule_rows = final_metrics.loc[final_metrics["method"].eq(selected_rule)].set_index(
        "trade_date"
    )
    assert candidate_rows.index.equals(rule_rows.index)
    conservative_difference = (
        candidate_rows["top_rank_lower"] - rule_rows["top_rank_upper"]
    ).to_numpy()
    generator = np.random.default_rng(seed)
    starts = generator.integers(
        0, len(final_dates), size=(10000, (len(final_dates) + 4) // 5)
    )
    indices = ((starts[:, :, None] + np.arange(5)) % len(final_dates)).reshape(
        10000, -1
    )[:, : len(final_dates)]
    means = conservative_difference[indices].mean(axis=1)
    lower95 = float(np.quantile(means, 0.05))
    coverage_candidate = float(
        candidate_rows["label_count"].sum() / candidate_rows["selected_count"].sum()
    )
    coverage_rule = float(
        rule_rows["label_count"].sum() / rule_rows["selected_count"].sum()
    )
    summary = {
        "candidate": selected_candidate,
        "comparator": selected_rule,
        "final_days": len(final_dates),
        "candidate_mean_top_rank_observed": float(
            candidate_rows["top_rank_observed"].mean()
        ),
        "comparator_mean_top_rank_observed": float(
            rule_rows["top_rank_observed"].mean()
        ),
        "mean_conservative_rank_difference": float(conservative_difference.mean()),
        "one_sided_95_lower_bound": lower95,
        "candidate_label_coverage": coverage_candidate,
        "comparator_label_coverage": coverage_rule,
        "candidate_mean_rank_ic": float(candidate_rows["rank_ic"].mean()),
        "comparator_mean_rank_ic": float(rule_rows["rank_ic"].mean()),
        "statistical_pilot_passed": bool(
            len(final_dates) >= 50
            and coverage_candidate >= 0.98
            and coverage_rule >= 0.98
            and conservative_difference.mean() >= 0.02
            and lower95 > 0
        ),
        "historical_real_time_alignment_verified": False,
        "net_alpha_verified": False,
        "limitations": [
            "readiness timestamps are assumed",
            "historical files may have revisions",
            "ranking target is not money",
            "no intraday subscription or execution replay",
            "only 79 final dates and 40 selection dates; no long-term stability claim",
        ],
    }
    _write_json(evidence / "final-summary.json", summary)
    for item in manifest:
        source_path = storage_root / item["path"]
        if _sha256(source_path) != item["sha256"]:
            raise RuntimeError(
                f"authoritative input changed since snapshot: {source_path}"
            )
    _write_json(
        evidence / "input-unchanged.json",
        {"verified_files": len(manifest), "all_content_unchanged": True},
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return evidence
