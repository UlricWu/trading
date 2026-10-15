# filepath: src/trading/backtest/reporting/bundle.py
from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path

from src import logs
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.utils.filesystem import FileSystem


@dataclass(frozen=True, slots=True)
class ReportingBundle:
    """Render an inspectable summary from validated persisted evaluations.

    Example:
        report = ReportingBundle(output_dir).run()
    """

    output_dir: Path

    def run(self) -> Path:
        """Show only actual task coverage and keep detailed facts in their artifacts.

        Example:
            report = ReportingBundle(output_dir).run()
        """
        ref = self.output_dir / "metrics.json"
        metrics = BacktestMetrics.model_validate_json(ref.read_bytes())
        sections: list[tuple[str, object]] = []
        execution_config = metrics.parameters.get("execution", {})
        if execution_config.get("fill", {}).get("type") == "quoted":
            sections.append(("成交假设", "合法接纳的订单在生效时按申报价全额成交，保留资金、数量、费用和 T+1 约束。"
                             "不检验市场触价、流动性或排队；期末持仓按当时可见行情估值，不强制平仓。"))
        if metrics.signal_eval is not None:
            sections.append(("信号评价", metrics.signal_eval.model_dump(exclude={"observations", "daily_ic"})))
        if metrics.risk_eval is not None:
            sections.append(("风控规则覆盖", metrics.risk_eval.model_dump(exclude={"observations"})))
        if metrics.execution_eval is not None:
            sections.append(("成交评价", metrics.execution_eval.model_dump(mode="json", exclude={"orders"})))
        if metrics.portfolio is not None:
            sections.append(("策略收益", metrics.portfolio.model_dump(mode="json", exclude={"daily_returns"})))
        if metrics.final_account is not None:
            sections.append(("最终账户", metrics.final_account.model_dump(mode="json")))
            sections.append(("本路径业务风控", metrics.applied_risk.model_dump(mode="json") if metrics.applied_risk else None))
        sections.append(("未运行能力", metrics.not_run))
        sections.append(("运行参数", metrics.parameters))
        body = "\n".join(f"<section><h2>{escape(title)}</h2><pre>{escape(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))}</pre></section>"
                         for title, value in sections)
        if metrics.risk_effectiveness is not None:
            effectiveness = metrics.risk_effectiveness
            rows = []
            for result in effectiveness.rules:
                amounts = ["无值" if value is None else f"{value:,.2f}" for value in (
                    result.max_required_reduction_notional, result.max_residual_reduction_notional,
                    result.final_residual_reduction_notional)]
                duration = ("无值" if result.sampled_breach_duration_us is None
                            else f"{result.sampled_breach_duration_us / 1_000_000:,.2f}")
                values = (result.rule.model_dump_json(), f"{result.defined_count}/{result.sample_count}",
                          str(result.observed_breach_count), str(result.residual_breach_count), *amounts, duration)
                rows.append("<tr>" + "".join(f"<td>{escape(value)}</td>" for value in values) + "</tr>")
            diagnostics = json.dumps(effectiveness.execution_reason_counts, ensure_ascii=False, indent=2)
            body = ('<section><h2>风控有效性：目标与实际账户</h2>'
                    '<p>暴露包含持仓和未成交买入承诺；未成交卖单不能抵扣持仓。'
                    '回撤阈值触发清仓，残余金额表示尚未消除的暴露，不保证亏损止于阈值。'
                    '有定义、未超限或未触发均不等于通过全部风控验证。</p>'
                    '<table><thead><tr><th>配置目标</th><th>有定义/观察数</th><th>检查前超限次数</th>'
                    '<th>执行后残余次数</th><th>检查前最大待减金额</th><th>执行后最大残余金额</th>'
                    '<th>期末残余金额</th><th>采样超限时长（秒）</th></tr></thead><tbody>'
                    + "".join(rows) + '</tbody></table>'
                    '<p>时长按左端样本累计至下一个有定义观察，包含跨日间隔，期末不外推；'
                    '不证明连续超限或精确响应时延。逐时点前后风险、无定义原因见 metrics.json。</p>'
                    '<h3>本账户执行原因</h3><pre>' + escape(diagnostics) + '</pre>'
                    '<p>按原始执行事件计数，可关联委托与成交事实追查；不是风控因果归因。'
                    '本账户费用、换手和收益是运行背景，不表示风控增量成本。</p></section>' + body)
        if metrics.final_account is not None:
            body += ('<p>交易明细：<a href="orders.json">委托</a> · '
                     '<a href="execution.parquet">成交与费用</a> · '
                     '<a href="equity.parquet">权益曲线</a> · '
                     '<a href="valuation-marks.parquet">持仓估值依据</a></p>')
        if metrics.data_quality.diagnostic_only:
            issues = json.dumps(metrics.data_quality.model_dump(mode="json"), ensure_ascii=False, indent=2)
            body = ('<section><h2>仅供诊断</h2><p>存在日线缺失且停复牌状态待核实的证券。'
                    '持仓可能沿用历史价格；收益与风险指标尚未验证，暂不成交也不保证收益更保守。'
                    '需核实并修复输入后重跑，后续恢复行情不会消除此前异常对账户路径的影响。</p>'
                    f'<pre>{escape(issues)}</pre></section>' + body)
        html = (f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>回测报告</title>'
                f'<body><h1>{escape(self.output_dir.name)}</h1><p>模式：{metrics.mode.value}</p>'
                '<p>null 表示无定义，原因随指标保存；空样本及规则未触发不表示验证通过。'
                '成交结论仅适用于声明的模拟假设。详细观察见 <a href="metrics.json">metrics.json</a> 和事实制品。</p>'
                f'{body}</body></html>')
        output = self.output_dir / "report.html"
        FileSystem.write_bytes_atomic(output, html.encode())
        lines = [f"模式：{metrics.mode.value}"]
        if metrics.data_quality.diagnostic_only:
            lines.append(f"仅供诊断：{len(metrics.data_quality.issues)} 个证券日缺少收盘价；收益与风险指标尚未验证，需修复输入后重跑。")
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
            lines.append(f"期末权益 {portfolio.final_equity:,.2f} 元；净利润 {portfolio.net_profit:+,.2f} 元；区间收益 {result}；最大回撤 {portfolio.max_drawdown:.4%}；夏普 {sharpe}")
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
        if metrics.risk_effectiveness is not None:
            for result in metrics.risk_effectiveness.rules:
                residual = result.final_residual_reduction_notional
                amount = "无值" if residual is None else f"{residual:,.2f} 元"
                lines.append(f"控制目标 {result.rule.model_dump_json()}：期末残余 {amount}；"
                             f"执行后有残余 {result.residual_breach_count}/{result.sample_count} 次")
        if metrics.not_run:
            lines.append(f"未运行能力：{json.dumps(metrics.not_run, ensure_ascii=False)}")
        lines.append(f"制品目录：{output.parent}；详细观察：metrics.json；报告：report.html")
        summary = "\n".join(lines)
        if metrics.data_quality.diagnostic_only:
            logs.warning(f"⚠️ 回测报告; experiment={self.output_dir.name}\n{summary}")
        else:
            logs.info(f"✅ 回测报告; experiment={self.output_dir.name}\n{summary}")
        return output
