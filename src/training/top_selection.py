# filepath: src/training/top_selection.py
"""Evaluate frozen top selections without letting missing labels change them."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from html import escape

import numpy as np
import pandas as pd
import pyarrow as pa

from src.access.feature_label import read_features, read_labels
from src.data_system.builders.previous_day import HIGH_EVENT_THRESHOLD, PREOPEN_RULES
from src.training.prediction import read_prediction_record
from src.utils.filesystem import FileSystem
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import PathManager


@dataclass(frozen=True)
class TopSelectionMetrics:
    """Keep original coverage alongside conditional and historical metrics.

    Example:
        metrics = TopSelectionMetrics(
            pool_size=2, pool_known=2, selected=1, selected_known=1,
            hits=1, opportunities=1, unknown_selected=0, unknown_unselected=0,
            pool_coverage=1.0, selection_coverage=1.0, precision=1.0, recall=1.0,
            f1=1.0, random_precision=0.5, lift=2.0, precision_lower=1.0, precision_upper=1.0,
        )
    """

    pool_size: int
    pool_known: int
    selected: int
    selected_known: int
    hits: int
    opportunities: int
    unknown_selected: int
    unknown_unselected: int
    pool_coverage: float | None
    selection_coverage: float | None
    precision: float | None
    recall: float | None
    f1: float | None
    random_precision: float | None
    lift: float | None
    precision_lower: float | None
    precision_upper: float | None


def select_top_symbols(symbols: Sequence[str], scores: np.ndarray, *, top_k: int) -> np.ndarray:
    """Freeze selection by descending score then symbol, with missing scores last.

    Example:
        positions = select_top_symbols(("000001", "000002"), np.array([0.8, 0.3]), top_k=1)
    """
    ranking = pd.DataFrame({"symbol": symbols, "score": scores, "position": np.arange(len(symbols))})
    return ranking.sort_values(["score", "symbol"], ascending=[False, True], na_position="last").position.iloc[:top_k].to_numpy()


def evaluate_top_selection(*, ranks: np.ndarray, selected: np.ndarray) -> TopSelectionMetrics:
    """Exclude unknown labels only after selection; never fill the empty slots.

    Example:
        metrics = evaluate_top_selection(ranks=np.array([np.nan, 1.0, 0.5]), selected=np.array([0, 1]))
        assert metrics.precision == 1.0
    """
    known = np.isfinite(ranks)
    events = known & (ranks > HIGH_EVENT_THRESHOLD)
    selected_known = int(known[selected].sum())
    pool_known = int(known.sum())
    hits = int(events[selected].sum())
    opportunities = int(events.sum())
    selected_count = len(selected)
    unknown_selected = selected_count - selected_known
    unknown_unselected = len(ranks) - pool_known - unknown_selected
    precision = hits / selected_known if selected_known else None
    random_precision = opportunities / pool_known if pool_known else None
    return TopSelectionMetrics(
        pool_size=len(ranks), pool_known=pool_known, selected=selected_count, selected_known=selected_known,
        hits=hits, opportunities=opportunities, unknown_selected=unknown_selected, unknown_unselected=unknown_unselected,
        pool_coverage=pool_known / len(ranks) if len(ranks) else None,
        selection_coverage=selected_known / selected_count if selected_count else None,
        precision=precision, recall=hits / opportunities if opportunities else None,
        f1=2 * hits / (selected_known + opportunities) if selected_known + opportunities else None,
        random_precision=random_precision,
        lift=precision / random_precision if precision is not None and random_precision else None,
        precision_lower=hits / selected_count if selected_count else None,
        precision_upper=(hits + unknown_selected) / selected_count if selected_count else None,
    )


def save_top_selection_evaluation(
    *, pm: PathManager, experiment_name: str, periods: Mapping[str, str], top_k: int = 50,
) -> None:
    """Evaluate saved predictions and the five fixed data-derived rule baselines.

    Example:
        save_top_selection_evaluation(pm=pm, experiment_name="training_run", periods={"2026-09-01": "evaluation"})
    """
    root = pm.experiment_training_dir(experiment_name=experiment_name)
    observations = []
    selections = []
    for day, period in periods.items():
        _, predictions = read_prediction_record(root / "predictions" / f"{day}.parquet")
        rules = read_features(pm=pm, feature_set="previous_day_daily_l2", version="v1", trade_date=day,
                              feature_names=tuple(PREOPEN_RULES.values()))
        keys = ["symbol", "trade_date", "decision_ts_utc"]
        if not predictions[keys].equals(rules[keys]):
            raise RuntimeError("selection rule keys differ from frozen prediction keys")
        scores = {"model": predictions.prediction.to_numpy(dtype=float)}
        scores.update({name: rules[column].to_numpy(dtype=float) for name, column in PREOPEN_RULES.items()})
        frozen = {name: select_top_symbols(predictions.symbol.tolist(), values, top_k=top_k) for name, values in scores.items()}
        labels = read_labels(pm=pm, label_set="next_day_high_return_rank", version="v1", trade_date=day, label_column="y_rank_return")
        if not predictions[keys].equals(labels[keys]):
            raise RuntimeError("selection label keys differ from frozen prediction keys")
        ranks = labels.y_rank_return.to_numpy(dtype=float)
        for name, positions in frozen.items():
            observations.append({"stage": period, "trade_date": day, "method": name,
                                 **asdict(evaluate_top_selection(ranks=ranks, selected=positions))})
            selections.append({"stage": period, "trade_date": day, "method": name,
                               "symbols": predictions.symbol.iloc[positions].tolist()})
    daily = pd.DataFrame(observations)
    summary = daily.groupby(["stage", "method"], sort=False).agg(
        days=("trade_date", "size"), hits=("hits", "sum"), selected_known=("selected_known", "sum"),
        unknown_selected=("unknown_selected", "sum"),
        pool_coverage=("pool_coverage", "mean"), selection_coverage=("selection_coverage", "mean"),
        precision=("precision", "mean"),
        recall=("recall", "mean"), f1=("f1", "mean"), random_precision=("random_precision", "mean"), lift=("lift", "mean"),
    ).reset_index()
    for name, frame in (("selection-metrics", daily), ("selections", pd.DataFrame(selections))):
        write_parquet_atomic(output_file=root / f"{name}.parquet", table=pa.Table.from_pandas(frame, preserve_index=False))
    FileSystem.write_bytes_atomic(root / "selection-summary.json", summary.to_json(orient="records", indent=2, double_precision=15).encode())
    html = (
        '<!doctype html><html lang="zh"><meta charset="utf-8"><title>训练评价</title>'
        '<style>body{font-family:sans-serif;margin:32px}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ddd}</style>'
        f'<h1>{escape(experiment_name)}</h1><p>固定 Top{top_k} 后剔除未知标签，不补选；主指标按日期等权。</p>'
        '<p>数据可用时间沿用前日22:00假设，不能证明严格历史可用性或可实现收益。</p>'
        + summary.to_html(index=False, float_format=lambda value: f"{value:.6f}") + '</html>'
    )
    FileSystem.write_bytes_atomic(pm.experiment_training_report(experiment_name=experiment_name), html.encode())
