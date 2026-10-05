# filepath: src/training/engines/report.py
"""Render every delivered model and every defined or undefined evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from pathlib import Path

from src.training.evaluation import ICEvaluation
from src.training.inference_model import InferenceModel


_REASON_LABELS = {
    "insufficient_pairs": "有效配对不足",
    "constant_prediction": "预测为常量",
    "constant_label": "标签为常量",
}


def build_training_report(
    *, experiment_name: str,
    models: Mapping[Path, InferenceModel], evaluation: ICEvaluation,
) -> str:
    """Return HTML from persisted model metadata and the evaluation schema.

    Example:
        html = build_training_report(
            experiment_name="training_run", models={model_ref: inference_model},
            evaluation=loaded_evaluation,
        )
    """
    summary = evaluation.summary
    mean = "无值" if summary.mean_ic is None else f"{summary.mean_ic:.6f}"
    model_rows = "".join(
        "<tr>"
        f"<td>{escape(str(ref))}</td>"
        f"<td>{escape(', '.join(model.train_dates))}</td>"
        f"<td>{escape(model.feature_set)} / {escape(model.feature_version)}</td>"
        f"<td>{escape(', '.join(model.feature_names))}</td>"
        f"<td>{escape(model.label_set)} / {escape(model.label_version)} / {escape(model.label_column)}</td>"
        "</tr>" for ref, model in models.items()
    )
    observation_rows: list[str] = []
    for item in evaluation.observations:
        value = "无值" if item.value is None else f"{item.value:.6f}"
        reasons = ", ".join(_REASON_LABELS[reason] for reason in item.undefined_reasons)
        observation_rows.append(
            "<tr>"
            f"<td>{item.decision_ts_us}</td><td>{escape(item.model_ref)}</td>"
            f"<td>{item.input_count}</td><td>{item.no_prediction_count}</td>"
            f"<td>{item.missing_label_count}</td><td>{item.pair_count}</td>"
            f"<td>{value}</td><td>{escape(reasons)}</td></tr>"
        )
    reason_counts = "、".join(
        f"{_REASON_LABELS[reason]}: {count}"
        for reason, count in summary.undefined_reason_counts.items()
    )
    title = escape(experiment_name)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui;margin:32px;color:#18202a}}table{{border-collapse:collapse;width:100%;margin:16px 0}}th,td{{border:1px solid #ccd3dc;padding:8px;text-align:left;overflow-wrap:anywhere}}th{{background:#eef2f6}}</style>
</head><body><h1>{title}</h1>
<p>Rank IC 均值：{mean}；有定义日期 / 全部评价日期：{summary.defined_date_count} / {summary.date_count}</p>
<p>{escape(reason_counts)}</p>
<h2>已交付模型</h2><table><thead><tr><th>模型引用</th><th>完整训练日期</th><th>特征身份</th><th>特征顺序</th><th>标签身份与列</th></tr></thead><tbody>{model_rows}</tbody></table>
<h2>逐日评价</h2><table><thead><tr><th>决策时点（UTC μs）</th><th>模型引用</th><th>输入行数</th><th>无预测</th><th>有预测但缺标签</th><th>有效配对</th><th>Rank IC</th><th>无值原因</th></tr></thead><tbody>{''.join(observation_rows)}</tbody></table>
</body></html>"""
