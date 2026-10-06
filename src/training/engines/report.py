# filepath: src/training/engines/report.py
"""Summarize fitted-model diagnostics and render complete training reports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from unicodedata import east_asian_width

import numpy as np

from src.config.model_config import ModelConfig
from src.jobs.requests import TrainingSubmission
from src.training.evaluation import ICEvaluation
from src.training.inference_model import InferenceModel
from src.utils.datetime_utils import DateTimeUtils


_REASON_LABELS = {
    "insufficient_pairs": "有效配对不足",
    "constant_prediction": "预测为常量",
    "constant_label": "标签为常量",
}


@dataclass(frozen=True, slots=True)
class RegressionMetrics:
    """Describe errors on finite pairs against rank labels and a constant 0.5 baseline.

    Example:
        errors = summary.evaluation
        assert errors.sample_count == 3
    """

    sample_count: int
    mae: float | None
    mse: float | None
    rmse: float | None
    r2: float | None
    baseline_rmse: float | None


@dataclass(frozen=True, slots=True)
class TrainingReportSummary:
    """Share the same computed diagnostics between the report and completion logs.

    Example:
        assert summary.positive_ic_days <= evaluation.summary.defined_date_count
        train_rmse = summary.train.rmse
    """

    train: RegressionMetrics
    evaluation: RegressionMetrics
    input_count: int
    no_prediction_count: int
    missing_label_count: int
    ic_std: float | None
    positive_ic_days: int
    positive_ic_ratio: float | None
    mae_gap: float | None
    rmse_gap: float | None
    rmse_vs_baseline: float | None


def summarize_training(
    *, train_predictions: np.ndarray, train_labels: np.ndarray,
    evaluation_predictions: np.ndarray, evaluation_labels: np.ndarray,
    evaluation: ICEvaluation,
) -> TrainingReportSummary:
    """Pool finite sample errors while giving each defined daily IC equal weight.

    Gaps are evaluation minus training errors. IC standard deviation uses ddof=1.

    Example:
        summary = summarize_training(
            train_predictions=np.array([0.2, 0.8]), train_labels=np.array([0.0, 1.0]),
            evaluation_predictions=np.array([0.3, 0.7]), evaluation_labels=np.array([0.0, 1.0]),
            evaluation=evaluation,
        )
    """
    train = _regression_metrics(train_predictions, train_labels)
    evaluated = _regression_metrics(evaluation_predictions, evaluation_labels)
    defined = np.array([item.value for item in evaluation.observations if item.value is not None])
    positive_days = int((defined > 0).sum())
    return TrainingReportSummary(
        train=train, evaluation=evaluated,
        input_count=sum(item.input_count for item in evaluation.observations),
        no_prediction_count=sum(item.no_prediction_count for item in evaluation.observations),
        missing_label_count=sum(item.missing_label_count for item in evaluation.observations),
        ic_std=float(np.std(defined, ddof=1)) if len(defined) >= 2 else None,
        positive_ic_days=positive_days,
        positive_ic_ratio=positive_days / len(defined) if len(defined) else None,
        mae_gap=evaluated.mae - train.mae if evaluated.mae is not None and train.mae is not None else None,
        rmse_gap=evaluated.rmse - train.rmse if evaluated.rmse is not None and train.rmse is not None else None,
        rmse_vs_baseline=(evaluated.rmse - evaluated.baseline_rmse
                          if evaluated.rmse is not None and evaluated.baseline_rmse is not None else None),
    )


def _regression_metrics(predictions: np.ndarray, labels: np.ndarray) -> RegressionMetrics:
    if predictions.ndim != 1 or labels.ndim != 1 or predictions.shape != labels.shape:
        raise ValueError("regression predictions and labels must be aligned one-dimensional arrays")
    if np.isinf(predictions).any() or np.isinf(labels).any():
        raise ValueError("regression predictions and labels must not contain infinity")
    paired = np.isfinite(predictions) & np.isfinite(labels)
    x, y = predictions[paired], labels[paired]
    if len(y) == 0:
        return RegressionMetrics(sample_count=0, mae=None, mse=None, rmse=None, r2=None, baseline_rmse=None)
    residuals = x - y
    mse = float(np.mean(residuals ** 2))
    total_square = float(np.sum((y - y.mean()) ** 2))
    metrics = RegressionMetrics(
        sample_count=len(y), mae=float(np.mean(np.abs(residuals))), mse=mse, rmse=float(np.sqrt(mse)),
        r2=1.0 - float(np.sum(residuals ** 2)) / total_square if len(y) >= 2 and total_square > 0 else None,
        baseline_rmse=float(np.sqrt(np.mean((0.5 - y) ** 2))),
    )
    if not all(np.isfinite(value) for value in (metrics.mae, metrics.mse, metrics.rmse, metrics.r2, metrics.baseline_rmse) if value is not None):
        raise RuntimeError("regression metrics returned an unexplained non-finite result")
    return metrics


def _metric(value: float | None) -> str:
    return "无值" if value is None else f"{value:.6f}"


def build_training_start_log(
    *, submission: TrainingSubmission, configuration: ModelConfig, target_description: str,
) -> str:
    """Explain the resolved request without dumping full parameters or column lists.

    Example:
        text = build_training_start_log(
            submission=submission, configuration=configuration,
            target_description="下一交易日复权收盘收益的横截面百分位排名",
        )
    """
    missing = configuration.preprocessing.missing
    missing_description = {
        "constant": f"用常量 {missing.fill_value} 填充",
        "mean": "用训练集各列均值填充",
        "median": "用训练集各列中位数填充",
        "drop": "训练移除缺失行；预测保留原行并标记无预测",
    }[missing.method]
    eval_range = submission.eval_range
    table = _text_table((
        ("项目", "本次选择"),
        ("模型", f"{submission.model}（SGD 线性回归）"),
        ("特征组", f"{submission.dataset.feature_group}（{len(configuration.dataset.feature_columns):,} 列）"),
        ("标签组", submission.dataset.label_group),
        ("请求训练区间", f"{submission.train_range.start} ~ {submission.train_range.end}"),
        ("请求评价区间", "本次未请求评价" if eval_range is None else f"{eval_range.start} ~ {eval_range.end}"),
        ("特征缺失处理", f"{missing.method}：{missing_description}"),
        ("训练方式", "一次 partial_fit（单轮）"),
    ))
    return (
        f"{table}\n预测目标：{target_description}。标签列：{configuration.dataset.label_column}。\n"
        "目标是排名分数，分数越高表示相对收益越高；不是收益率。\n"
        "完整参数、特征顺序和实际交易日将在校验通过后写入运行配置 schedule.json。"
    )


def _result_interpretation(
    evaluation: ICEvaluation, summary: TrainingReportSummary, actual_iterations: int,
) -> tuple[str, str, str]:
    mean_ic = evaluation.summary.mean_ic
    if mean_ic is None:
        ranking = "排序评价：无值（没有 IC 有定义的评价日）。"
    else:
        direction = "为正" if mean_ic > 0 else "为负" if mean_ic < 0 else "为零"
        ranking = (
            f"排序评价：平均每日 Rank IC {direction}（{mean_ic:.6f}）；"
            f"正 IC {summary.positive_ic_days:,} / {evaluation.summary.defined_date_count:,} 个有效交易日"
            f"（{summary.positive_ic_ratio:.2%}）。"
        )
    gap = summary.rmse_vs_baseline
    baseline = summary.evaluation.baseline_rmse
    if gap is None or baseline is None:
        regression = "回归评价：无值（没有有效预测与标签配对）。"
    else:
        direction = "高" if gap > 0 else "低" if gap < 0 else "相同"
        if baseline == 0:
            relative = "相对变化无值：基线 RMSE 为 0"
        elif gap == 0:
            relative = "相同，0.00%"
        else:
            relative = f"{direction} {abs(gap) / baseline:.2%}"
        regression = (
            f"回归评价：评估 RMSE {_metric(summary.evaluation.rmse)}，固定预测 0.5 基线 {_metric(baseline)}；"
            f"模型 − 基线 = {gap:+.6f}（{relative}）。"
        )
    return (
        ranking, regression,
        f"训练诊断：实际训练 {actual_iterations:,} 轮；逐轮损失未记录，收敛尚未评估。",
    )


def build_training_log(
    *, model: InferenceModel, evaluation: ICEvaluation, summary: TrainingReportSummary,
    actual_iterations: int, model_ref: Path, metrics_ref: Path, report_ref: Path, schedule_ref: Path,
) -> str:
    """Render readable, aligned diagnostic tables for one multiline log event.

    Example:
        text = build_training_log(
            model=model, evaluation=evaluation, summary=summary, actual_iterations=1,
            model_ref=model_ref, metrics_ref=metrics_ref, report_ref=report_ref, schedule_ref=schedule_ref,
        )
    """
    coverage = evaluation.summary
    eval_dates = [
        DateTimeUtils.to_local(DateTimeUtils.from_utc_epoch_us(item.decision_ts_us)).date().isoformat()
        for item in evaluation.observations
    ]
    pair_ratio = f"{summary.evaluation.sample_count / summary.input_count:.2%}" if summary.input_count else "无值：无输入样本"
    ic_ratio = f"{coverage.defined_date_count / coverage.date_count:.2%}"
    positive_ratio = f"{summary.positive_ic_ratio:.2%}" if summary.positive_ic_ratio is not None else "无值"
    overview = _text_table((
        ("项目", "结果"),
        ("训练日期", f"{model.train_dates[0]} ~ {model.train_dates[-1]}（{len(model.train_dates):,} 个交易日）"),
        ("实际训练样本 / 特征数", f"{summary.train.sample_count:,} 行 / {len(model.feature_names):,} 列"),
        ("实际训练轮数", f"{actual_iterations:,}"),
        ("评价日期", f"{eval_dates[0]} ~ {eval_dates[-1]}（{coverage.date_count:,} 个交易日）"),
        ("有效配对 / 评价输入", f"{summary.evaluation.sample_count:,} / {summary.input_count:,} 行（{pair_ratio}）"),
        ("评价排除明细", f"无预测 {summary.no_prediction_count:,} 行；有预测但缺标签 {summary.missing_label_count:,} 行"),
        ("IC 有效日期 / 评价日期", f"{coverage.defined_date_count:,} / {coverage.date_count:,} 个交易日（{ic_ratio}）"),
    ))
    reasons = "、".join(
        f"{_REASON_LABELS[reason]} {count:,} 个交易日" for reason, count in coverage.undefined_reason_counts.items()
    ) or "无"
    ic_table = _text_table((
        ("指标", "结果"),
        ("IC 均值", _metric(coverage.mean_ic)),
        ("IC 标准差（ddof=1）", _metric(summary.ic_std)),
        ("正 IC 天数 / IC 有效日期", f"{summary.positive_ic_days:,} / {coverage.defined_date_count:,}（{positive_ratio}）"),
        ("IC 无值原因", reasons),
    ))
    error_table = _text_table((
        ("指标", "训练集", "评估集", "评估 − 训练"),
        ("MAE（平均绝对误差）", _metric(summary.train.mae), _metric(summary.evaluation.mae),
         "无值" if summary.mae_gap is None else f"{summary.mae_gap:+.6f}"),
        ("MSE（均方误差）", _metric(summary.train.mse), _metric(summary.evaluation.mse), "—"),
        ("RMSE（均方根误差）", _metric(summary.train.rmse), _metric(summary.evaluation.rmse),
         "无值" if summary.rmse_gap is None else f"{summary.rmse_gap:+.6f}"),
        ("R²（决定系数）", _metric(summary.train.r2), _metric(summary.evaluation.r2), "—"),
        ("固定预测 0.5 基线 RMSE", _metric(summary.train.baseline_rmse), _metric(summary.evaluation.baseline_rmse), "—"),
    ))
    interpretation = "\n".join(_result_interpretation(evaluation, summary, actual_iterations))
    return (
        f"{interpretation}\n\n训练与评价覆盖（Asia/Shanghai）\n{overview}\n\n"
        f"排序表现（Rank IC）\n{ic_table}\n\n"
        f"误差与基线（排名分数）\n{error_table}\n"
        "MAE / MSE / RMSE 越小误差越小；差距为评估减训练，— 表示不展示该项差距；差值和比例按原始精度计算。\n"
        "R² 相对各自样本集的标签均值基线；负值表示误差大于该基线，与固定预测 0.5 的比较不同。\n"
        "IC 按有定义日期等权，误差按有效配对样本等权；排序方向与数值误差可以表现不同。\n"
        "正 IC 日比例不等于预测准确率或交易胜率；训练差距不能单独判定过拟合。\n\n"
        f"制品路径\n模型：{model_ref}\nIC 指标 JSON：{metrics_ref}\n完整诊断 HTML 报告：{report_ref}\n运行配置：{schedule_ref}"
    )


def _text_table(rows: Sequence[Sequence[str]]) -> str:
    cell_widths = [
        [sum(2 if east_asian_width(char) in {"W", "F"} else 1 for char in cell) for cell in row]
        for row in rows
    ]
    widths = [max(column) for column in zip(*cell_widths, strict=True)]
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    lines = [border]
    for index, (row, sizes) in enumerate(zip(rows, cell_widths, strict=True)):
        cells = [f" {cell}{' ' * (width - size)} " for cell, width, size in zip(row, widths, sizes, strict=True)]
        lines.append("|" + "|".join(cells) + "|")
        if index == 0:
            lines.append(border)
    lines.append(border)
    return "\n".join(lines)


def build_training_report(
    *, experiment_name: str, model_ref: Path, model: InferenceModel,
    evaluation: ICEvaluation, summary: TrainingReportSummary, actual_iterations: int,
) -> str:
    """Render fitted training diagnostics and fixed-prediction evaluation in HTML.

    Example:
        html = build_training_report(
            experiment_name="training_run", model_ref=model_ref, model=inference_model,
            evaluation=loaded_evaluation, summary=summary, actual_iterations=1,
        )
    """
    coverage = evaluation.summary
    model_row = (
        "<tr>"
        f"<td>{escape(str(model_ref))}</td>"
        f"<td>{escape(', '.join(model.train_dates))}</td>"
        f"<td>{escape(model.feature_set)} / {escape(model.feature_version)}</td>"
        f"<td>{escape(', '.join(model.feature_names))}</td>"
        f"<td>{escape(model.label_set)} / {escape(model.label_version)} / {escape(model.label_column)}</td>"
        "</tr>"
    )
    regression_rows = "".join(
        f"<tr><td>{name}</td><td>{item.sample_count}</td>"
        f"<td>{_metric(item.mae)}</td><td>{_metric(item.mse)}</td><td>{_metric(item.rmse)}</td>"
        f"<td>{_metric(item.r2)}</td><td>{_metric(item.baseline_rmse)}</td></tr>"
        for name, item in (("训练集", summary.train), ("评估集", summary.evaluation))
    )
    observation_rows: list[str] = []
    for item in evaluation.observations:
        value = _metric(item.value)
        reasons = ", ".join(_REASON_LABELS[reason] for reason in item.undefined_reasons)
        decision_time = DateTimeUtils.to_local(DateTimeUtils.from_utc_epoch_us(item.decision_ts_us)).isoformat(sep=" ")
        observation_rows.append(
            "<tr>"
            f"<td>{decision_time}</td><td>{escape(item.model_ref)}</td>"
            f"<td>{item.input_count}</td><td>{item.no_prediction_count}</td>"
            f"<td>{item.missing_label_count}</td><td>{item.pair_count}</td>"
            f"<td>{value}</td><td>{escape(reasons)}</td></tr>"
        )
    reason_counts = "、".join(
        f"{_REASON_LABELS[reason]}: {count}"
        for reason, count in coverage.undefined_reason_counts.items()
    )
    title = escape(experiment_name)
    interpretation = "".join(f"<p>{escape(line)}</p>" for line in _result_interpretation(evaluation, summary, actual_iterations))
    positive_ratio = f"{summary.positive_ic_ratio:.2%}" if summary.positive_ic_ratio is not None else "无值"
    pair_ratio = f"{summary.evaluation.sample_count / summary.input_count:.2%}" if summary.input_count else "无值：无输入样本"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui;margin:32px;color:#18202a}}table{{border-collapse:collapse;width:100%;margin:16px 0}}th,td{{border:1px solid #ccd3dc;padding:8px;text-align:left;overflow-wrap:anywhere}}th{{background:#eef2f6}}</style>
</head><body><h1>{title}</h1>
<h2>训练与评价摘要</h2>
{interpretation}
<p>实际训练样本：{summary.train.sample_count}；特征数：{len(model.feature_names)}；实际训练轮数：{actual_iterations}</p>
<p>Rank IC 均值：{_metric(coverage.mean_ic)}；IC 标准差（ddof=1）：{_metric(summary.ic_std)}；正 IC 天数 / 有定义日期：{summary.positive_ic_days} / {coverage.defined_date_count}（{positive_ratio}）</p>
<p>有定义日期 / 全部评价日期：{coverage.defined_date_count} / {coverage.date_count} 个交易日；评价输入行数：{summary.input_count}；无预测：{summary.no_prediction_count}；有预测但缺标签：{summary.missing_label_count}；有效配对：{summary.evaluation.sample_count} 行（{pair_ratio}）</p>
<p>{escape(reason_counts)}</p>
<h2>回归误差与训练诊断</h2>
<table><thead><tr><th>样本集</th><th>有效样本</th><th>MAE</th><th>MSE</th><th>RMSE</th><th>R²</th><th>固定预测 0.5 基线 RMSE</th></tr></thead><tbody>{regression_rows}</tbody></table>
<p>评估 − 训练 MAE：{_metric(summary.mae_gap)}；评估 − 训练 RMSE：{_metric(summary.rmse_gap)}；评估 RMSE − 基线 RMSE：{_metric(summary.rmse_vs_baseline)}（负值表示模型误差更小）。差值和比例按原始精度计算。</p>
<p>IC 按有定义日期等权统计；回归误差合并全部有效配对、按样本等权计算。零配对时误差无值；R² 在不足两个样本或标签为常量时无值；IC 标准差在不足两个有定义日期时无值。</p>
<p>误差针对排名标签，不是收益率。训练误差为拟合内误差，训练与评估差距不能单独判定过拟合。未记录逐轮损失，无法据此判断收敛；实际轮数不代表配置的 max_iter。</p>
<p>R² 相对各自样本集的标签均值基线；负值表示误差大于该基线，与固定预测 0.5 的比较不同。正 IC 日比例不等于预测准确率或交易胜率；排序方向与数值误差可以表现不同。</p>
<h2>已交付模型</h2><table><thead><tr><th>模型引用</th><th>完整训练日期</th><th>特征身份</th><th>特征顺序</th><th>标签身份与列</th></tr></thead><tbody>{model_row}</tbody></table>
<h2>逐日评价</h2><table><thead><tr><th>决策时点（Asia/Shanghai）</th><th>模型引用</th><th>输入行数</th><th>无预测</th><th>有预测但缺标签</th><th>有效配对</th><th>Rank IC</th><th>无值原因</th></tr></thead><tbody>{''.join(observation_rows)}</tbody></table>
</body></html>"""
