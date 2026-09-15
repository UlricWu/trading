# filepath: src/trading/reporting/bundle.py
from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path

from src import logs
from src.observability.log_format import format_log_json
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class ReportingBundle:
    """
    Generate the formal backtest HTML report from experiment artifacts.

    The current report boundary is intentionally small: read summary metrics
    from `backtest/metrics.json`, render its top-level key/value pairs, and
    write `report/backtest_report.html`. This class does not read runtime
    context, scan detail Parquet artifacts, or write CSV artifacts.

    Example:
        report_path = ReportingBundle(pm=path_manager).run(
            experiment_name="backtest_2026-07-01_2026-07-20_run-1",
        )
    """

    pm: PathManager

    def run(self, *, experiment_name: str) -> Path:
        """Generate `backtest_report.html` for one experiment.

        Example:
            report_path = ReportingBundle(pm=path_manager).run(
                experiment_name="backtest_2026-07-01_2026-07-20_run-1",
            )
        """
        metrics_path = self.pm.experiment_backtest_metrics(
            experiment_name=experiment_name,
        )
        report_path = self.pm.experiment_backtest_report(
            experiment_name=experiment_name,
        )

        metrics = _load_metrics(metrics_path)
        html = _render_metrics_html(
            experiment_name=experiment_name,
            metrics=metrics,
        )
        FileSystem.write_bytes_atomic(report_path, html.encode("utf-8"))
        logs.info(
            f"✅ backtest report publish; path={report_path}\n"
            f"{format_log_json('backtest_metrics', metrics)}"
        )
        return report_path


def _load_metrics(metrics_path: Path) -> dict[str, object]:
    if not metrics_path.is_file():
        raise FileNotFoundError(f"backtest metrics not found: {metrics_path}")

    try:
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid backtest metrics JSON: {metrics_path}") from exc

    if not isinstance(data, dict):
        raise ValueError("backtest metrics must be a JSON object")
    return {str(key): value for key, value in data.items()}


def _render_metrics_html(
    *,
    experiment_name: str,
    metrics: dict[str, object],
) -> str:
    rows = "\n".join(
        "          <tr>"
        f"<th>{escape(str(key))}</th>"
        f"<td>{escape(json.dumps(value, ensure_ascii=False, sort_keys=True))}</td>"
        "</tr>"
        for key, value in sorted(metrics.items())
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        "    <title>Backtest Report</title>\n"
        "  </head>\n"
        "  <body>\n"
        "    <h1>Backtest Report</h1>\n"
        f"    <p>Experiment: {escape(experiment_name)}</p>\n"
        "    <table>\n"
        "      <thead>\n"
        "        <tr><th>Metric</th><th>Value</th></tr>\n"
        "      </thead>\n"
        "      <tbody>\n"
        f"{rows}\n"
        "      </tbody>\n"
        "    </table>\n"
        "  </body>\n"
        "</html>\n"
    )
