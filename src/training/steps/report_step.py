# filepath: src/training/steps/report_step.py
from __future__ import annotations

import json
import math
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

from src import logs
from src.observability.log_format import format_log_json
from src.training.context import TrainingContext
from src.utils.filesystem import FileSystem


_IC_KEY = re.compile(r"^ic@(\d{4}-\d{2}-\d{2})$")
_REQUIRED_PARAMS = (
    "experiment_name",
    "experiment_id",
    "model_group",
    "asof_day",
    "created_at",
    "feature_set",
    "feature_version",
    "feature_names",
    "label_set",
    "label_version",
    "label_column",
    "label_lookahead",
    "price_adjustment",
)


@dataclass(frozen=True, slots=True)
class TrainingReportParams:
    """Validated identity and dataset fields rendered in a training report."""

    experiment_name: str
    experiment_id: str
    model_group: str
    asof_day: str
    created_at: str
    feature_set: str
    feature_version: str
    feature_names: tuple[str, ...]
    label_set: str
    label_version: str
    label_column: str
    label_lookahead: int
    price_adjustment: str


@dataclass(frozen=True, slots=True)
class RankICRow:
    eval_date: str
    rank_ic: float
    rolling_5: float | None
    rolling_20: float | None


@dataclass(frozen=True, slots=True)
class RankICSummary:
    observations: int
    eval_start: str
    eval_end: str
    mean_ic: float
    median_ic: float
    std_ic: float | None
    t_stat: float | None
    positive_ratio: float
    min_ic: float
    min_ic_date: str
    max_ic: float
    max_ic_date: str
    last_5_mean: float
    rolling_20_last: float | None


class ReportStep:
    """
    Generate the offline training HTML report from persisted artifacts.

    The step only reads `training/params.json` and `training/metrics.json`,
    then writes `report/training_report.html`. It does not read runtime
    predictions, model files, preprocess files, feature/label detail, or any
    old Rank IC report engine.

    Example:
        step = ReportStep()
        step(context)
    """

    def __call__(self, ctx: TrainingContext) -> None:
        """Generate the persisted training report.

        Example:
            step(context)
        """
        if not ctx.experiment_name:
            raise RuntimeError("[ReportStep] experiment_name is required")

        params_path = ctx.pm.experiment_training_params(
            experiment_name=ctx.experiment_name,
        )
        metrics_path = ctx.pm.experiment_training_metrics(
            experiment_name=ctx.experiment_name,
        )
        report_path = ctx.pm.experiment_training_report(
            experiment_name=ctx.experiment_name,
        )

        raw_params = _load_json_object(params_path, label="training params")
        metrics = _load_json_object(metrics_path, label="training metrics")
        params = _parse_params(raw_params)

        rows = _rank_ic_rows(metrics)
        summary = _rank_ic_summary(rows)
        html = _render_html(
            experiment_name=ctx.experiment_name,
            params=params,
            summary=summary,
            rows=rows,
        )
        FileSystem.write_bytes_atomic(report_path, html.encode("utf-8"))
        logs.info(
            f"[ReportStep] saved path={report_path}\n"
            f"{format_log_json('rank_ic_summary', summary)}"
        )


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON: {path}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a JSON object")
    return {str(key): value for key, value in data.items()}


def _parse_params(params: dict[str, object]) -> TrainingReportParams:
    missing = [key for key in _REQUIRED_PARAMS if key not in params]
    if missing:
        raise ValueError(f"training params missing required fields: {missing}")
    feature_names_raw = params["feature_names"]
    if (
        not isinstance(feature_names_raw, list)
        or not feature_names_raw
        or any(not isinstance(name, str) or not name for name in feature_names_raw)
    ):
        raise ValueError("training params feature_names must be non-empty strings")
    label_lookahead = params["label_lookahead"]
    if (
        isinstance(label_lookahead, bool)
        or not isinstance(label_lookahead, int)
        or label_lookahead < 0
    ):
        raise ValueError("training params label_lookahead must be non-negative")
    string_values = {
        key: _required_string(params, key)
        for key in _REQUIRED_PARAMS
        if key not in {"feature_names", "label_lookahead"}
    }
    return TrainingReportParams(
        **string_values,
        feature_names=tuple(feature_names_raw),
        label_lookahead=label_lookahead,
    )


def _required_string(params: dict[str, object], key: str) -> str:
    value = params[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"training params {key} must be a non-empty string")
    return value


def _rank_ic_rows(metrics: dict[str, object]) -> list[RankICRow]:
    pairs: list[tuple[str, float]] = []
    for key, raw_value in metrics.items():
        match = _IC_KEY.fullmatch(str(key))
        if match is None:
            continue

        eval_date = match.group(1)
        try:
            date.fromisoformat(eval_date)
        except ValueError as exc:
            raise ValueError(f"invalid training metric date: {key}") from exc

        if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
            raise ValueError(f"training metric {key} must be a finite number")
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(f"training metric {key} must be a finite number")
        pairs.append((eval_date, value))

    if not pairs:
        raise ValueError("training metrics must contain ic@YYYY-MM-DD values")

    pairs.sort(key=lambda item: item[0])
    values = [value for _, value in pairs]
    rolling_5 = _rolling_means(values, 5)
    rolling_20 = _rolling_means(values, 20)

    rows: list[RankICRow] = []
    for idx, (eval_date, value) in enumerate(pairs):
        rows.append(
            RankICRow(
                eval_date=eval_date,
                rank_ic=value,
                rolling_5=rolling_5[idx],
                rolling_20=rolling_20[idx],
            )
        )
    return rows


def _rolling_means(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for idx in range(len(values)):
        start = idx - window + 1
        if start < 0:
            out.append(None)
        else:
            out.append(sum(values[start : idx + 1]) / window)
    return out


def _rank_ic_summary(rows: Sequence[RankICRow]) -> RankICSummary:
    values = [row.rank_ic for row in rows]
    count = len(values)
    mean_ic = sum(values) / count
    std_ic = statistics.stdev(values) if count > 1 else None
    t_stat = (
        mean_ic / (std_ic / math.sqrt(count))
        if std_ic is not None and std_ic > 0.0
        else None
    )
    min_row = min(rows, key=lambda row: row.rank_ic)
    max_row = max(rows, key=lambda row: row.rank_ic)
    return RankICSummary(
        observations=count,
        eval_start=rows[0].eval_date,
        eval_end=rows[-1].eval_date,
        mean_ic=mean_ic,
        median_ic=statistics.median(values),
        std_ic=std_ic,
        t_stat=t_stat,
        positive_ratio=sum(1 for value in values if value > 0.0) / count,
        min_ic=min_row.rank_ic,
        min_ic_date=min_row.eval_date,
        max_ic=max_row.rank_ic,
        max_ic_date=max_row.eval_date,
        last_5_mean=sum(values[-5:]) / min(5, count),
        rolling_20_last=rows[-1].rolling_20,
    )


def _render_html(
    *,
    experiment_name: str,
    params: TrainingReportParams,
    summary: RankICSummary,
    rows: Sequence[RankICRow],
) -> str:
    context_rows = _table_rows(
        [
            ("experiment_name", experiment_name),
            ("experiment_id", params.experiment_id),
            ("model_group", params.model_group),
            ("asof_day", params.asof_day),
            ("created_at", params.created_at),
            ("feature_set", f"{params.feature_set} / {params.feature_version}"),
            ("feature_names", _format_value(params.feature_names)),
            ("label_set", f"{params.label_set} / {params.label_version}"),
            ("label_column", params.label_column),
            ("label_lookahead", params.label_lookahead),
            ("price_adjustment", params.price_adjustment),
        ]
    )
    summary_rows = _table_rows(
        [
            ("observations", summary.observations),
            ("eval_start", summary.eval_start),
            ("eval_end", summary.eval_end),
            ("mean_ic", _format_number(summary.mean_ic)),
            ("median_ic", _format_number(summary.median_ic)),
            ("std_ic", _format_number(summary.std_ic)),
            ("t_stat", _format_number(summary.t_stat)),
            ("positive_ratio", _format_percent(summary.positive_ratio)),
            (
                "min_ic",
                f"{_format_number(summary.min_ic)} @ {summary.min_ic_date}",
            ),
            (
                "max_ic",
                f"{_format_number(summary.max_ic)} @ {summary.max_ic_date}",
            ),
            ("last_5_mean", _format_number(summary.last_5_mean)),
            ("rolling_20_last", _format_number(summary.rolling_20_last)),
        ]
    )
    ic_rows = "\n".join(
        "          <tr>"
        f"<td>{escape(row.eval_date)}</td>"
        f'<td class="num {_sign_class(row.rank_ic)}">'
        f"{_format_number(row.rank_ic)}</td>"
        f'<td class="num {_sign_class(row.rolling_5)}">'
        f"{_format_number(row.rolling_5)}</td>"
        f'<td class="num {_sign_class(row.rolling_20)}">'
        f"{_format_number(row.rolling_20)}</td>"
        "</tr>"
        for row in rows
    )

    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        "    <title>Training Report</title>\n"
        "    <style>\n"
        "      body { font-family: Arial, sans-serif; margin: 32px; color: #1f2933; }\n"
        "      h1, h2 { margin: 0 0 12px; }\n"
        "      section { margin: 0 0 28px; }\n"
        "      table { border-collapse: collapse; width: 100%; font-size: 14px; }\n"
        "      th, td { border: 1px solid #d8dee4; padding: 8px 10px; text-align: left; }\n"
        "      th { background: #f6f8fa; }\n"
        "      .num { text-align: right; font-variant-numeric: tabular-nums; }\n"
        "      .pos { color: #116329; }\n"
        "      .neg { color: #b42318; }\n"
        "      .muted { color: #667085; }\n"
        "    </style>\n"
        "  </head>\n"
        "  <body>\n"
        "    <h1>Training Report</h1>\n"
        "    <section>\n"
        "      <h2>Run Overview</h2>\n"
        "      <table><tbody>\n"
        f"{context_rows}\n"
        "      </tbody></table>\n"
        "    </section>\n"
        "    <section>\n"
        "      <h2>Rank IC Summary</h2>\n"
        "      <table><tbody>\n"
        f"{summary_rows}\n"
        "      </tbody></table>\n"
        "    </section>\n"
        "    <section>\n"
        "      <h2>Rank IC Series</h2>\n"
        "      <table>\n"
        "        <thead>\n"
        "          <tr><th>eval_date</th><th>rank_ic</th><th>rolling_5</th><th>rolling_20</th></tr>\n"
        "        </thead>\n"
        "        <tbody>\n"
        f"{ic_rows}\n"
        "        </tbody>\n"
        "      </table>\n"
        "    </section>\n"
        "  </body>\n"
        "</html>\n"
    )


def _table_rows(items: Sequence[tuple[str, object]]) -> str:
    return "\n".join(
        "        <tr>"
        f"<th>{escape(key)}</th>"
        f"<td>{escape(_format_value(value))}</td>"
        "</tr>"
        for key, value in items
    )


def _format_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _format_number(value: object) -> str:
    if value is None:
        return ""
    number = float(value)
    if not math.isfinite(number):
        return ""
    return f"{number:.6f}"


def _format_percent(value: float) -> str:
    return f"{value * 100.0:.1f}%"


def _sign_class(value: object) -> str:
    if value is None:
        return "muted"
    number = float(value)
    if number > 0.0:
        return "pos"
    if number < 0.0:
        return "neg"
    return "muted"
