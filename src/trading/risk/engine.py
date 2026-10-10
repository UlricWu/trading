# filepath: src/trading/risk/engine.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.trading.core.events import Side
from src.trading.risk.base import RiskContext, RiskDecision, RiskRule, RuleObservation, TargetPositions


@dataclass(frozen=True, slots=True)
class RiskManager:
    """Apply ordered rules to a request or to current outstanding commitments.

    Example:
        manager = RiskManager((MaxSingleNameNotionalRule(max_ratio=0.2),))
        decision = manager.apply(target={"600000": 1000}, ctx=context)
    """

    rules: Sequence[RiskRule]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", tuple(self.rules))
        if not self.rules:
            raise ValueError("risk requires a nonempty rule chain")

    def apply(self, *, target: TargetPositions | None, ctx: RiskContext) -> RiskDecision:
        """Reduce current commitments on timer checks without reviving old targets.

        Example:
            decision = manager.apply(target=None, ctx=current_account_snapshot)
        """
        committed = dict(ctx.positions)
        for pending in ctx.pending_orders:
            order = pending.intent
            quantity = pending.remaining_qty if order.side == Side.BUY else -pending.remaining_qty
            committed[order.symbol] = committed.get(order.symbol, 0) + quantity
        current = dict(committed if target is None else target)
        if not committed.keys() <= current.keys():
            raise ValueError("risk target must cover positions and pending orders")
        if any(type(qty) is not int or qty < 0 for qty in current.values()):
            raise ValueError("risk target quantities must be non-negative integers")
        if not current.keys() <= ctx.prices.keys() or not current.keys() <= ctx.quantity_steps.keys():
            raise ValueError("risk requires prices and quantity steps for every controlled symbol")

        observations: list[RuleObservation] = []
        if ctx.equity <= 0:
            reduced = {symbol: min(qty, ctx.positions.get(symbol, 0)) for symbol, qty in current.items()}
            observations.append(RuleObservation("nonpositive_equity", current, reduced,
                current != reduced or any(p.intent.side == Side.BUY for p in ctx.pending_orders),
                "no_new_buys"))
            current = reduced
        for rule in self.rules:
            observation = rule.apply(target=current, ctx=ctx)
            observations.append(observation)
            current = dict(observation.after)

        cancellations = tuple(p.intent.order_id for p in ctx.pending_orders
            if p.intent.side == Side.BUY and (
                ctx.equity <= 0 or current[p.intent.symbol] < committed[p.intent.symbol]
            ))
        if target is None and current == committed:
            adjusted = None
            reason = "no_action"
        elif target is not None and current != target and all(
            qty == ctx.positions.get(symbol, 0) for symbol, qty in current.items()
        ) and not any(p.intent.side == Side.SELL for p in ctx.pending_orders):
            adjusted = None
            reason = "rejected"
        else:
            adjusted = current
            reason = "adjusted" if current != target else "allowed"
        return RiskDecision(adjusted, cancellations, reason, tuple(observations))
