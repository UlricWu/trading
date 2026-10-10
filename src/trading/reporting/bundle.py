# filepath: src/trading/reporting/bundle.py
from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import cast

from src import logs
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class ReportingBundle:
    """
    Generate the formal backtest HTML report from experiment artifacts.

    The current report boundary is intentionally small: read summary metrics
    from `metrics.json`, render its top-level key/value pairs, and
    write `report.html` in the same experiment root. This class does not read runtime
    context, scan detail Parquet artifacts, or write CSV artifacts.

    Example:
        report_path = ReportingBundle(pm=path_manager).run(
            experiment_name="backtest_2026-07-01_2026-07-20_run-1",
        )
    """

    pm: PathManager

    def run(self, *, experiment_name: str) -> Path:
        """Generate `report.html` for one experiment.

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
            f"✅ 回测报告; experiment={experiment_name}\n"
            f"{_render_metrics_log(metrics)}\n"
            f"制品目录：{report_path.parent}\n"
            f"  predictions/       每日预测明细\n"
            f"  execution.parquet  执行与成交明细\n"
            f"  equity.parquet     权益与回撤明细\n"
            f"  metrics.json       完整指标与运行参数\n"
            f"  report.html        HTML 报告"
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


def _format_metric(value: object, spec: str) -> str:
    return "无值" if value is None else format(value, spec)


def _render_metrics_log(metrics: dict[str, object]) -> str:
    model_evaluation = cast(dict[str, object], metrics["model_evaluation"])
    model_summary = cast(dict[str, object], model_evaluation["summary"])
    signal = cast(dict[str, object], metrics["signal_eval"])
    tradable = cast(dict[str, object], metrics["tradable_alpha_eval"])
    risk = cast(dict[str, object], metrics["risk_eval"])
    positions = cast(dict[str, int], metrics["final_positions"])
    no_orders = "（本次未提交订单）" if metrics["order_submits"] == 0 else ""
    return "\n".join((
        f"决策日期：{metrics['start_date']} ~ {metrics['end_date']}（{metrics['trade_days']:,} 个交易日）",
        "日期口径：请求区间最后一个交易日用于次日评价，不作为决策日。",
        f"账户：期末权益 {_format_metric(metrics['final_equity'], ',.2f')} 元；"
        f"现金 {_format_metric(metrics['final_cash'], ',.2f')} 元；持仓 {len(positions):,} 只",
        f"表现：区间收益 {_format_metric(metrics['total_return'], '+.4%')}；"
        f"最大回撤 {_format_metric(metrics['max_drawdown'], '.4%')}；夏普 {_format_metric(metrics['sharpe'], '.4f')}",
        f"订单：提交 {metrics['order_submits']:,.0f} 笔；完全成交 {metrics['completed_orders']:,.0f} 笔；"
        f"拒绝 {metrics['order_rejects']:,.0f} 笔{no_orders}",
        f"成交数量：{metrics['filled_qty']:,.0f} / {metrics['requested_qty']:,.0f} 股"
        f"（{_format_metric(metrics['qty_fill_rate'], '.2%')}）",
        f"费用合计：{_format_metric(metrics['cost_total'], ',.2f')} 元"
        f"（佣金 {_format_metric(metrics['cost_commission'], ',.2f')}；"
        f"税费 {_format_metric(metrics['cost_tax'], ',.2f')}；滑点 {_format_metric(metrics['cost_slippage'], ',.2f')}）",
        f"模型目标 Rank IC：{_format_metric(model_summary['mean_ic'], '.6f')}"
        f"（有效 {model_summary['defined_date_count']:,} / {model_summary['date_count']:,} 日）",
        f"次日原始收盘收益 Rank IC：{_format_metric(signal['mean_rank_ic'], '.6f')}"
        f"（有效 {signal['rank_ic_defined_date_count']:,} / {signal['frames']:,} 日）；有效评分 {signal['score_count']:,} 行",
        f"可交易样本：{tradable['tradable_count']:,} / {tradable['sample_count']:,} 行"
        f"（{_format_metric(tradable['tradable_coverage'], '.2%')}）",
        f"风控影响：调整 {risk['changed_targets']:,} 个目标；阻断 {risk['blocked_count']:,} 日；缩放 {risk['scaled_count']:,} 日",
    ))


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
