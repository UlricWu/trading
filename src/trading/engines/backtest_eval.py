# filepath: src/trading/engines/backtest_eval.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import NotRequired, TypedDict

from src.trading.core.equity import EquityPoint
from src.trading.core.events import FillEvent


class SignalEvalFrame(TypedDict):
    score_count: int
    label_count: int
    coverage: float | None
    failed_missing_entry_price: int
    failed_invalid_entry_price: int
    failed_missing_exit_price: int
    failed_invalid_exit_price: int
    ic_pearson: float | None
    rank_ic: float | None
    mean_forward_return: float | None
    positive_ratio: float | None
    top_quantile_return: float | None
    bottom_quantile_return: float | None
    long_short_return: float | None
    quantile_returns: dict[str, float]
    trade_date: NotRequired[str]
    forward_date: NotRequired[str]
    ts_us: NotRequired[int]


class TradableAlphaFrame(TypedDict):
    sample_count: int
    tradable_count: int
    failed_count: int
    tradable_coverage: float | None
    round_trip_cost_bps: float
    failed_missing_entry_price: int
    failed_invalid_entry_price: int
    failed_missing_exit_price: int
    failed_invalid_exit_price: int
    mean_gross_return: float | None
    mean_net_return: float | None
    net_positive_ratio: float | None
    top_quantile_net_return: float | None
    bottom_quantile_net_return: float | None
    long_short_net_return: float | None
    quantile_net_returns: dict[str, float]
    trade_date: NotRequired[str]
    forward_date: NotRequired[str]
    ts_us: NotRequired[int]


class RiskEffectFrame(TypedDict):
    target_count_before: int
    target_count_after: int
    changed_targets: int
    dropped_targets: int
    added_targets: int
    resized_targets: int
    blocked: bool
    scaled: bool
    reason: str
    gross_notional_before: float
    gross_notional_after: float
    gross_exposure_before: float | None
    gross_exposure_after: float | None
    net_notional_before: float
    net_notional_after: float
    unpriced_targets_before: int
    unpriced_targets_after: int
    trade_date: NotRequired[str]
    ts_us: NotRequired[int]


class ExecutionQualityFrame(TypedDict):
    requested_qty: int
    buy_requested_qty: int
    sell_requested_qty: int
    requested_notional: float
    unpriced_requests: int
    filled_qty: int
    buy_filled_qty: int
    sell_filled_qty: int
    filled_notional: float
    unfilled_qty: int
    qty_fill_rate: float | None
    order_submits: int
    order_rejects: int
    fill_records: int
    order_fill_rate: float | None
    order_reject_rate: float | None
    slippage_cost: float
    cost_total: float
    slippage_bps: float | None
    cost_bps: float | None
    trade_date: NotRequired[str]
    ts_us: NotRequired[int]
    ledger_records_added: NotRequired[int]


class FullBacktestFrame(TypedDict):
    positions: int
    gross_position_qty: int
    cash: float
    market_value: float
    equity: float
    drawdown: float
    period_return: float | None
    total_return: float | None
    trade_date: NotRequired[str]
    ts_us: NotRequired[int]


def signal_eval_frame(
    *,
    scores: Mapping[str, float],
    entry_prices: Mapping[str, float],
    exit_prices: Mapping[str, float],
    quantiles: int = 5,
) -> SignalEvalFrame:
    """Evaluate score quality against the current T+1 raw-close label."""
    if quantiles < 2:
        raise RuntimeError("[signal_eval] quantiles must be >= 2")
    rows, counts = _score_return_rows(
        scores=scores,
        entry_prices=entry_prices,
        exit_prices=exit_prices,
        round_trip_cost_bps=0.0,
        label="signal_eval",
    )
    frame: SignalEvalFrame = {
        "score_count": int(len(scores)),
        "label_count": int(len(rows)),
        "coverage": _ratio(len(rows), len(scores)),
        "failed_missing_entry_price": counts["missing_entry_price"],
        "failed_invalid_entry_price": counts["invalid_entry_price"],
        "failed_missing_exit_price": counts["missing_exit_price"],
        "failed_invalid_exit_price": counts["invalid_exit_price"],
        "ic_pearson": None,
        "rank_ic": None,
        "mean_forward_return": None,
        "positive_ratio": None,
        "top_quantile_return": None,
        "bottom_quantile_return": None,
        "long_short_return": None,
        "quantile_returns": {},
    }
    if not rows:
        return frame

    score_values = [row["score"] for row in rows]
    returns = [row["gross_return"] for row in rows]
    quantile_returns = _quantile_means(
        rows=rows,
        value_key="gross_return",
        quantiles=quantiles,
    )
    bottom_key = "q1"
    top_key = f"q{quantiles}"
    top_return = quantile_returns.get(top_key)
    bottom_return = quantile_returns.get(bottom_key)
    frame.update(
        {
            "ic_pearson": _corr(score_values, returns),
            "rank_ic": _corr(_rank_average(score_values), _rank_average(returns)),
            "mean_forward_return": _mean(returns),
            "positive_ratio": _ratio(
                sum(1 for value in returns if value > 0.0),
                len(returns),
            ),
            "top_quantile_return": top_return,
            "bottom_quantile_return": bottom_return,
            "long_short_return": (
                None
                if top_return is None or bottom_return is None
                else float(top_return - bottom_return)
            ),
            "quantile_returns": quantile_returns,
        }
    )
    return frame


def tradable_alpha_frame(
    *,
    scores: Mapping[str, float],
    entry_prices: Mapping[str, float],
    exit_prices: Mapping[str, float],
    round_trip_cost_bps: float = 0.0,
    quantiles: int = 5,
) -> TradableAlphaFrame:
    """Build the T+1 tradable alpha label summary from raw-close prices."""
    if quantiles < 2:
        raise RuntimeError("[tradable_alpha] quantiles must be >= 2")
    rows, counts = _score_return_rows(
        scores=scores,
        entry_prices=entry_prices,
        exit_prices=exit_prices,
        round_trip_cost_bps=round_trip_cost_bps,
        label="tradable_alpha",
    )
    failed_count = int(len(scores) - len(rows))
    frame: TradableAlphaFrame = {
        "sample_count": int(len(scores)),
        "tradable_count": int(len(rows)),
        "failed_count": failed_count,
        "tradable_coverage": _ratio(len(rows), len(scores)),
        "round_trip_cost_bps": float(round_trip_cost_bps),
        "failed_missing_entry_price": counts["missing_entry_price"],
        "failed_invalid_entry_price": counts["invalid_entry_price"],
        "failed_missing_exit_price": counts["missing_exit_price"],
        "failed_invalid_exit_price": counts["invalid_exit_price"],
        "mean_gross_return": None,
        "mean_net_return": None,
        "net_positive_ratio": None,
        "top_quantile_net_return": None,
        "bottom_quantile_net_return": None,
        "long_short_net_return": None,
        "quantile_net_returns": {},
    }
    if not rows:
        return frame

    gross_returns = [row["gross_return"] for row in rows]
    net_returns = [row["net_return"] for row in rows]
    quantile_returns = _quantile_means(
        rows=rows,
        value_key="net_return",
        quantiles=quantiles,
    )
    bottom_key = "q1"
    top_key = f"q{quantiles}"
    top_return = quantile_returns.get(top_key)
    bottom_return = quantile_returns.get(bottom_key)
    frame.update(
        {
            "mean_gross_return": _mean(gross_returns),
            "mean_net_return": _mean(net_returns),
            "net_positive_ratio": _ratio(
                sum(1 for value in net_returns if value > 0.0),
                len(net_returns),
            ),
            "top_quantile_net_return": top_return,
            "bottom_quantile_net_return": bottom_return,
            "long_short_net_return": (
                None
                if top_return is None or bottom_return is None
                else float(top_return - bottom_return)
            ),
            "quantile_net_returns": quantile_returns,
        }
    )
    return frame


def risk_effect_frame(
    *,
    targets_before: Mapping[str, int],
    targets_after: Mapping[str, int],
    prices: Mapping[str, float],
    equity: float,
    blocked: bool,
    scaled: bool,
    reason: str,
) -> RiskEffectFrame:
    """Measure how risk changed target facts without interpreting execution."""
    before = _require_targets(targets_before, "targets_before")
    after = _require_targets(targets_after, "targets_after")
    equity_value = _require_finite(equity, "equity")

    symbols = sorted(set(before) | set(after))
    changed = 0
    dropped = 0
    added = 0
    resized = 0
    for symbol in symbols:
        before_qty = before.get(symbol, 0)
        after_qty = after.get(symbol, 0)
        if before_qty == after_qty:
            continue
        changed += 1
        if before_qty > 0 and after_qty == 0:
            dropped += 1
        elif before_qty == 0 and after_qty > 0:
            added += 1
        else:
            resized += 1

    before_notional = _target_notional(targets=before, prices=prices)
    after_notional = _target_notional(targets=after, prices=prices)
    return {
        "target_count_before": int(sum(1 for qty in before.values() if qty > 0)),
        "target_count_after": int(sum(1 for qty in after.values() if qty > 0)),
        "changed_targets": int(changed),
        "dropped_targets": int(dropped),
        "added_targets": int(added),
        "resized_targets": int(resized),
        "blocked": bool(blocked),
        "scaled": bool(scaled),
        "reason": str(reason),
        "gross_notional_before": before_notional["gross_notional"],
        "gross_notional_after": after_notional["gross_notional"],
        "gross_exposure_before": _ratio(before_notional["gross_notional"], equity_value),
        "gross_exposure_after": _ratio(after_notional["gross_notional"], equity_value),
        "net_notional_before": before_notional["net_notional"],
        "net_notional_after": after_notional["net_notional"],
        "unpriced_targets_before": before_notional["unpriced_targets"],
        "unpriced_targets_after": after_notional["unpriced_targets"],
    }


def execution_quality_frame(
    *,
    targets: Mapping[str, int],
    positions_before: Mapping[str, int],
    prices: Mapping[str, float],
    fills: Sequence[FillEvent],
    ledger_records: Sequence[Mapping[str, object]],
) -> ExecutionQualityFrame:
    """Measure execution quality from targets, fills and new ledger facts."""
    target_map = _require_targets(targets, "targets")
    position_map = _require_targets(positions_before, "positions_before")

    requested_qty = 0
    requested_notional = 0.0
    unpriced_requests = 0
    buy_requested_qty = 0
    sell_requested_qty = 0
    for symbol, target_qty in target_map.items():
        current_qty = position_map.get(symbol, 0)
        delta = int(target_qty) - int(current_qty)
        if delta == 0:
            continue
        abs_delta = abs(delta)
        requested_qty += abs_delta
        if delta > 0:
            buy_requested_qty += abs_delta
        else:
            sell_requested_qty += abs_delta
        price = _optional_positive_price(prices, symbol)
        if price is None:
            unpriced_requests += 1
        else:
            requested_notional += abs_delta * price

    fill_qty = 0
    fill_notional = 0.0
    buy_fill_qty = 0
    sell_fill_qty = 0
    for fill in fills:
        qty = int(fill.qty)
        price = _require_positive(fill.price, "fill.price")
        if qty <= 0:
            raise RuntimeError("[execution_quality] fill qty must be positive")
        fill_qty += qty
        fill_notional += qty * price
        if fill.side.value == "BUY":
            buy_fill_qty += qty
        elif fill.side.value == "SELL":
            sell_fill_qty += qty

    submits = 0
    rejects = 0
    fill_records = 0
    slippage_cost = 0.0
    cost_total = 0.0
    for record in ledger_records:
        event = record.get("event")
        if event == "ORDER_SUBMIT":
            submits += 1
        elif event == "ORDER_REJECT":
            rejects += 1
        elif event == "FILL":
            fill_records += 1
            meta = record.get("meta", {}) or {}
            if not isinstance(meta, Mapping):
                raise RuntimeError("[execution_quality] ledger meta must be a mapping")
            slippage_cost += _mapping_float(meta, "slippage_cost", default=0.0)
            cost = meta.get("cost", {}) or {}
            if not isinstance(cost, Mapping):
                raise RuntimeError("[execution_quality] ledger cost must be a mapping")
            cost_total += _mapping_float(cost, "total", default=0.0)

    return {
        "requested_qty": int(requested_qty),
        "buy_requested_qty": int(buy_requested_qty),
        "sell_requested_qty": int(sell_requested_qty),
        "requested_notional": float(requested_notional),
        "unpriced_requests": int(unpriced_requests),
        "filled_qty": int(fill_qty),
        "buy_filled_qty": int(buy_fill_qty),
        "sell_filled_qty": int(sell_fill_qty),
        "filled_notional": float(fill_notional),
        "unfilled_qty": int(max(0, requested_qty - fill_qty)),
        "qty_fill_rate": _ratio(fill_qty, requested_qty),
        "order_submits": int(submits),
        "order_rejects": int(rejects),
        "fill_records": int(fill_records),
        "order_fill_rate": _ratio(fill_records, submits),
        "order_reject_rate": _ratio(rejects, submits),
        "slippage_cost": float(slippage_cost),
        "cost_total": float(cost_total),
        "slippage_bps": _ratio(slippage_cost * 10_000.0, fill_notional),
        "cost_bps": _ratio(cost_total * 10_000.0, fill_notional),
    }


def full_backtest_frame(
    *,
    equity_points: Sequence[EquityPoint],
    positions: Mapping[str, int],
) -> FullBacktestFrame:
    """Create a per-timing full-backtest valuation frame."""
    if not equity_points:
        raise RuntimeError("[full_backtest] equity points required")
    position_map = _require_targets(positions, "positions")
    latest = equity_points[-1]
    previous = equity_points[-2] if len(equity_points) >= 2 else None
    first = equity_points[0]
    return {
        "positions": int(sum(1 for qty in position_map.values() if qty > 0)),
        "gross_position_qty": int(sum(abs(qty) for qty in position_map.values())),
        "cash": float(latest.cash),
        "market_value": float(latest.market_value),
        "equity": float(latest.equity),
        "drawdown": float(latest.drawdown),
        "period_return": (
            None
            if previous is None or float(previous.equity) <= 0.0
            else float(latest.equity / previous.equity - 1.0)
        ),
        "total_return": (
            None
            if float(first.equity) <= 0.0
            else float(latest.equity / first.equity - 1.0)
        ),
    }


def summarize_signal_eval_frames(
    frames: Sequence[SignalEvalFrame],
) -> dict[str, object]:
    return {
        "frames": int(len(frames)),
        "score_count": _sum_int(frames, "score_count"),
        "label_count": _sum_int(frames, "label_count"),
        "coverage": _weighted_ratio(frames, "label_count", "score_count"),
        "mean_rank_ic": _mean_present(frames, "rank_ic"),
        "mean_ic_pearson": _mean_present(frames, "ic_pearson"),
        "mean_forward_return": _mean_present(frames, "mean_forward_return"),
        "mean_long_short_return": _mean_present(frames, "long_short_return"),
    }


def summarize_tradable_alpha_frames(
    frames: Sequence[TradableAlphaFrame],
) -> dict[str, object]:
    return {
        "frames": int(len(frames)),
        "sample_count": _sum_int(frames, "sample_count"),
        "tradable_count": _sum_int(frames, "tradable_count"),
        "failed_count": _sum_int(frames, "failed_count"),
        "tradable_coverage": _weighted_ratio(frames, "tradable_count", "sample_count"),
        "mean_gross_return": _mean_present(frames, "mean_gross_return"),
        "mean_net_return": _mean_present(frames, "mean_net_return"),
        "mean_long_short_net_return": _mean_present(frames, "long_short_net_return"),
    }


def summarize_risk_effect_frames(
    frames: Sequence[RiskEffectFrame],
) -> dict[str, object]:
    return {
        "frames": int(len(frames)),
        "blocked_count": _sum_bool(frames, "blocked"),
        "scaled_count": _sum_bool(frames, "scaled"),
        "changed_targets": _sum_int(frames, "changed_targets"),
        "dropped_targets": _sum_int(frames, "dropped_targets"),
        "added_targets": _sum_int(frames, "added_targets"),
        "mean_gross_exposure_before": _mean_present(frames, "gross_exposure_before"),
        "mean_gross_exposure_after": _mean_present(frames, "gross_exposure_after"),
    }


def summarize_execution_quality_frames(
    frames: Sequence[ExecutionQualityFrame],
) -> dict[str, object]:
    requested_qty = _sum_int(frames, "requested_qty")
    filled_qty = _sum_int(frames, "filled_qty")
    submits = _sum_int(frames, "order_submits")
    rejects = _sum_int(frames, "order_rejects")
    fill_records = _sum_int(frames, "fill_records")
    filled_notional = _sum_float(frames, "filled_notional")
    slippage_cost = _sum_float(frames, "slippage_cost")
    cost_total = _sum_float(frames, "cost_total")
    return {
        "frames": int(len(frames)),
        "requested_qty": int(requested_qty),
        "filled_qty": int(filled_qty),
        "qty_fill_rate": _ratio(filled_qty, requested_qty),
        "order_submits": int(submits),
        "order_rejects": int(rejects),
        "fill_records": int(fill_records),
        "order_fill_rate": _ratio(fill_records, submits),
        "order_reject_rate": _ratio(rejects, submits),
        "filled_notional": float(filled_notional),
        "slippage_cost": float(slippage_cost),
        "cost_total": float(cost_total),
        "slippage_bps": _ratio(slippage_cost * 10_000.0, filled_notional),
        "cost_bps": _ratio(cost_total * 10_000.0, filled_notional),
    }


def summarize_full_backtest_frames(
    frames: Sequence[FullBacktestFrame],
) -> dict[str, object]:
    latest = frames[-1] if frames else {}
    return {
        "frames": int(len(frames)),
        "final_equity": _optional_float(latest.get("equity")),
        "final_cash": _optional_float(latest.get("cash")),
        "final_market_value": _optional_float(latest.get("market_value")),
        "final_positions": _optional_int(latest.get("positions")),
        "total_return": _optional_float(latest.get("total_return")),
        "max_drawdown": abs(min(_present_numbers(frames, "drawdown"), default=0.0)),
        "mean_period_return": _mean_present(frames, "period_return"),
    }


def _score_return_rows(
    *,
    scores: Mapping[str, float],
    entry_prices: Mapping[str, float],
    exit_prices: Mapping[str, float],
    round_trip_cost_bps: float,
    label: str,
) -> tuple[list[dict[str, float]], dict[str, int]]:
    if not scores:
        raise RuntimeError(f"[{label}] scores required")
    if not entry_prices:
        raise RuntimeError(f"[{label}] entry_prices required")
    if not exit_prices:
        raise RuntimeError(f"[{label}] exit_prices required")
    cost_bps = _require_finite(round_trip_cost_bps, "round_trip_cost_bps")
    if cost_bps < 0.0:
        raise RuntimeError(f"[{label}] round_trip_cost_bps must be non-negative")

    rows: list[dict[str, float]] = []
    counts = {
        "missing_entry_price": 0,
        "invalid_entry_price": 0,
        "missing_exit_price": 0,
        "invalid_exit_price": 0,
    }
    for symbol, score in scores.items():
        sym = str(symbol)
        score_value = _require_finite(score, f"{label}.score[{sym}]")
        entry = _required_price_or_count(
            prices=entry_prices,
            symbol=sym,
            missing_key="missing_entry_price",
            invalid_key="invalid_entry_price",
            counts=counts,
        )
        if entry is None:
            continue
        exit_ = _required_price_or_count(
            prices=exit_prices,
            symbol=sym,
            missing_key="missing_exit_price",
            invalid_key="invalid_exit_price",
            counts=counts,
        )
        if exit_ is None:
            continue
        gross_return = float(exit_ / entry - 1.0)
        rows.append(
            {
                "score": score_value,
                "gross_return": gross_return,
                "net_return": float(gross_return - cost_bps * 1e-4),
            }
        )
    return rows, counts


def _required_price_or_count(
    *,
    prices: Mapping[str, float],
    symbol: str,
    missing_key: str,
    invalid_key: str,
    counts: dict[str, int],
) -> float | None:
    if symbol not in prices:
        counts[missing_key] += 1
        return None
    value = prices[symbol]
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0.0:
        counts[invalid_key] += 1
        return None
    return float(value)


def _require_targets(targets: Mapping[str, int], label: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for symbol, qty in targets.items():
        if (
            isinstance(qty, bool)
            or not isinstance(qty, (int, float))
            or not math.isfinite(float(qty))
            or float(qty) != int(qty)
        ):
            raise RuntimeError(
                f"[{label}] target qty must be an integer: {symbol}"
            )
        qty_int = int(qty)
        if qty_int < 0:
            raise RuntimeError(f"[{label}] target qty must be non-negative: {symbol}")
        out[str(symbol)] = qty_int
    return out


def _target_notional(
    *,
    targets: Mapping[str, int],
    prices: Mapping[str, float],
) -> dict[str, float | int]:
    gross = 0.0
    net = 0.0
    unpriced = 0
    for symbol, qty in targets.items():
        if int(qty) == 0:
            continue
        price = _optional_positive_price(prices, symbol)
        if price is None:
            unpriced += 1
            continue
        notional = int(qty) * price
        gross += abs(notional)
        net += notional
    return {
        "gross_notional": float(gross),
        "net_notional": float(net),
        "unpriced_targets": int(unpriced),
    }


def _quantile_means(
    *,
    rows: Sequence[Mapping[str, float]],
    value_key: str,
    quantiles: int,
) -> dict[str, float]:
    if quantiles < 2:
        raise RuntimeError("[backtest_eval] quantiles must be >= 2")
    ordered = sorted(rows, key=lambda row: row["score"])
    n = len(ordered)
    out: dict[str, float] = {}
    for index in range(quantiles):
        start = (index * n) // quantiles
        end = ((index + 1) * n) // quantiles
        if start == end:
            continue
        out[f"q{index + 1}"] = _mean([row[value_key] for row in ordered[start:end]])
    return out


def _rank_average(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(indexed)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for original_index, _value in indexed[cursor:end]:
            ranks[original_index] = average_rank
        cursor = end
    return ranks


def _corr(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right):
        raise RuntimeError("[backtest_eval] correlation length mismatch")
    if len(left) < 2:
        return None
    left_mean = _mean(left)
    right_mean = _mean(right)
    left_dev = [value - left_mean for value in left]
    right_dev = [value - right_mean for value in right]
    left_var = _mean([value * value for value in left_dev])
    right_var = _mean([value * value for value in right_dev])
    if left_var <= 0.0 or right_var <= 0.0:
        return None
    covariance = _mean([a * b for a, b in zip(left_dev, right_dev, strict=True)])
    return float(covariance / math.sqrt(left_var * right_var))


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise RuntimeError("[backtest_eval] mean requires values")
    return float(sum(float(value) for value in values) / len(values))


def _ratio(numerator: float, denominator: float) -> float | None:
    if float(denominator) <= 0.0:
        return None
    return float(numerator) / float(denominator)


def _optional_positive_price(prices: Mapping[str, float], symbol: str) -> float | None:
    value = prices.get(str(symbol))
    if value is None:
        return None
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0.0:
        return None
    return float(value)


def _require_positive(value: float, label: str) -> float:
    number = _require_finite(value, label)
    if number <= 0.0:
        raise RuntimeError(f"[backtest_eval] {label} must be positive")
    return number


def _require_finite(value: float, label: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise RuntimeError(f"[backtest_eval] {label} must be finite")
    return float(value)


def _mapping_float(
    mapping: Mapping[str, object],
    key: str,
    *,
    default: float,
) -> float:
    value = mapping.get(key, default)
    return _require_finite(value, key)


def _present_numbers(
    frames: Sequence[Mapping[str, object]],
    key: str,
) -> list[float]:
    out: list[float] = []
    for frame in frames:
        value = frame.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            out.append(float(value))
    return out


def _mean_present(
    frames: Sequence[Mapping[str, object]],
    key: str,
) -> float | None:
    values = _present_numbers(frames, key)
    if not values:
        return None
    return _mean(values)


def _sum_int(frames: Sequence[Mapping[str, object]], key: str) -> int:
    total = 0
    for frame in frames:
        value = frame.get(key, 0)
        if isinstance(value, bool):
            total += int(value)
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            total += int(value)
    return int(total)


def _sum_bool(frames: Sequence[Mapping[str, object]], key: str) -> int:
    return int(sum(1 for frame in frames if bool(frame.get(key, False))))


def _sum_float(frames: Sequence[Mapping[str, object]], key: str) -> float:
    return float(sum(_present_numbers(frames, key)))


def _weighted_ratio(
    frames: Sequence[Mapping[str, object]],
    numerator_key: str,
    denominator_key: str,
) -> float | None:
    numerator = _sum_int(frames, numerator_key)
    denominator = _sum_int(frames, denominator_key)
    return _ratio(numerator, denominator)


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return int(value)
    return None
