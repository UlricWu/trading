# filepath: src/training/engines/report.py
"""Build the deterministic HTML representation of a training report."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape

from src.training.engines.rank_ic import (
    RankICRow,
    RankICSummary,
    build_rank_ic_rows,
    summarize_rank_ic,
)

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
class TrainingReport:
    """Contain rendered training HTML and its Rank IC summary.

    Example:
        report = TrainingReport(
            html="<html></html>",
            summary=rank_ic_summary,
        )
    """

    html: str
    summary: RankICSummary


@dataclass(frozen=True, slots=True)
class _TrainingReportParams:
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


def build_training_report(
    *,
    experiment_name: str,
    params_payload: Mapping[str, object],
    metrics_payload: Mapping[str, object],
) -> TrainingReport:
    """Build training report HTML from persisted JSON-compatible objects.

    Example:
        report = build_training_report(
            experiment_name="training_2026-07-01_2026-07-20_run-1",
            params_payload=params,
            metrics_payload={"ic@2026-07-20": 0.1},
        )
    """
    params = _parse_params(params_payload)
    rows = build_rank_ic_rows(metrics_payload)
    summary = summarize_rank_ic(rows)
    return TrainingReport(
        html=_render_html(
            experiment_name=experiment_name,
            params=params,
            summary=summary,
            rows=rows,
        ),
        summary=summary,
    )


def _parse_params(params: Mapping[str, object]) -> _TrainingReportParams:
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
    return _TrainingReportParams(
        **string_values,
        feature_names=tuple(feature_names_raw),
        label_lookahead=label_lookahead,
    )


def _required_string(params: Mapping[str, object], key: str) -> str:
    value = params[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"training params {key} must be a non-empty string")
    return value


def _render_html(
    *,
    experiment_name: str,
    params: _TrainingReportParams,
    summary: RankICSummary,
    rows: Sequence[RankICRow],
) -> str:
    context_rows = _table_rows(
        (
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
        )
    )
    summary_rows = _table_rows(
        (
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
        )
    )
    rank_ic_rows = "\n".join(
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
        f"{rank_ic_rows}\n"
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


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    number = float(value)
    if not math.isfinite(number):
        return ""
    return f"{number:.6f}"


def _format_percent(value: float) -> str:
    return f"{value * 100.0:.1f}%"


def _sign_class(value: float | None) -> str:
    if value is None:
        return "muted"
    number = float(value)
    if number > 0.0:
        return "pos"
    if number < 0.0:
        return "neg"
    return "muted"
