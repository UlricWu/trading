# filepath: src/trading/backtest/reporting/bundle.py
from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path

from src import logs
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.utils.filesystem import FileSystem
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class ReportingBundle:
    """Render an inspectable summary from validated persisted evaluations.

    Example:
        report = ReportingBundle(pm).run(experiment_name=experiment_name)
    """

    pm: PathManager

    def run(self, *, experiment_name: str) -> Path:
        """Show only actual task coverage and keep detailed facts in their artifacts.

        Example:
            report = ReportingBundle(pm).run(experiment_name=experiment_name)
        """
        ref = self.pm.experiment_backtest_metrics(experiment_name=experiment_name)
        metrics = BacktestMetrics.model_validate_json(ref.read_bytes())
        sections: list[tuple[str, object]] = []
        if metrics.signal_eval is not None:
            sections.append(("信号评价", metrics.signal_eval.model_dump(exclude={"observations", "daily_ic"})))
        if metrics.risk_eval is not None:
            sections.append(("风控评价", metrics.risk_eval.model_dump(exclude={"observations"})))
        if metrics.execution_eval is not None:
            sections.append(("成交评价", metrics.execution_eval.model_dump(mode="json", exclude={"orders"})))
        if metrics.portfolio is not None:
            sections.append(("策略收益", metrics.portfolio.model_dump(mode="json", exclude={"daily_returns"})))
        if metrics.final_account is not None:
            sections.append(("最终账户", metrics.final_account.model_dump(mode="json")))
        sections.append(("未运行能力", metrics.not_run))
        sections.append(("运行参数", metrics.parameters))
        body = "\n".join(f"<section><h2>{escape(title)}</h2><pre>{escape(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))}</pre></section>"
                         for title, value in sections)
        html = (f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>回测报告</title>'
                f'<body><h1>{escape(experiment_name)}</h1><p>模式：{metrics.mode.value}</p>'
                '<p>null 表示无定义，原因随指标保存；空样本及规则未触发不表示验证通过。'
                '成交结论仅适用于声明的模拟假设。详细观察见 <a href="metrics.json">metrics.json</a> 和事实制品。</p>'
                f'{body}</body></html>')
        output = self.pm.experiment_backtest_report(experiment_name=experiment_name)
        FileSystem.write_bytes_atomic(output, html.encode())
        lines = [f"模式：{metrics.mode.value}"]
        if metrics.signal_eval is not None:
            signal = metrics.signal_eval
            value = "无值" if signal.mean_ic is None else f"{signal.mean_ic:.6f}"
            lines.append(f"模型目标 Rank IC：{value}（有效 {signal.defined_day_count} / {signal.trade_day_count} 日；{signal.decision_count} 次决策）")
            if signal.undefined_reason_counts:
                lines.append(f"信号无值原因：{json.dumps(signal.undefined_reason_counts, ensure_ascii=False)}")
        if metrics.portfolio is not None:
            portfolio = metrics.portfolio
            result = "无值" if portfolio.total_return is None else f"{portfolio.total_return:+.4%}"
            sharpe = "无值" if portfolio.sharpe is None else f"{portfolio.sharpe:.4f}"
            lines.append(f"期末权益 {portfolio.final_equity:,.2f} 元；区间收益 {result}；最大回撤 {portfolio.max_drawdown:.4%}；夏普 {sharpe}")
            lines.append(f"费用合计：{portfolio.total_fees:,.2f} 元")
            if portfolio.undefined_reasons:
                lines.append(f"组合无值原因：{json.dumps(portfolio.undefined_reasons, ensure_ascii=False)}")
        if metrics.execution_eval is not None:
            execution = metrics.execution_eval
            lines.append(f"订单：提交 {execution.order_count} 笔；接纳 {execution.accepted_count} 笔；完全成交 {execution.completed_count} 笔")
            if not execution.order_count:
                lines.append("本次未提交订单")
            rate = "无值" if execution.quantity_fill_rate is None else f"{execution.quantity_fill_rate:.2%}"
            lines.append(f"成交数量：{execution.filled_qty:,} / {execution.requested_qty:,} 股（{rate}）")
        if metrics.risk_eval is not None:
            risk = metrics.risk_eval
            lines.append(f"风控：{risk.case_count} 次检查；{risk.request_count} 次策略请求；调整 {risk.adjusted_count} 次；拒绝 {risk.rejected_count} 次")
            if not risk.case_count:
                lines.append("风控无样本")
            for name, coverage in risk.rule_coverage.items():
                lines.append(f"规则 {name}：检查 {coverage.checked} 次；触发 {coverage.triggered} 次")
        if metrics.not_run:
            lines.append(f"未运行能力：{json.dumps(metrics.not_run, ensure_ascii=False)}")
        lines.append(f"制品目录：{output.parent}；详细观察：metrics.json；报告：report.html")
        summary = "\n".join(lines)
        logs.info(f"✅ 回测报告; experiment={experiment_name}\n{summary}")
        return output
