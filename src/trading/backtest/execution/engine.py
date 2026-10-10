# filepath: src/trading/backtest/execution/engine.py
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal

from src.trading.core.events import OrderIntent, Side, FillEvent
from src.trading.core.ledger import ExecutionLedger
from src.trading.execution.policies.constraints import AShareTargetClippingPolicy
from src.trading.execution.policies.validation import AShareOrderValidation
from src.trading.backtest.execution.settlement import SettlementEngine, affordable_quantity
from src.trading.backtest.execution.venue.sim_immediate import SimImmediateVenue
from src.trading.backtest.market.data_view import MarketDataView
from src.trading.portfolio.state import PortfolioState


def _optional_limits(
    data_view: MarketDataView,
    symbol: str,
) -> Mapping[str, object] | None:
    get_limits = getattr(data_view, "get_limits", None)
    if get_limits is None:
        return None
    if not callable(get_limits):
        raise TypeError("data_view.get_limits must be callable")
    limits = get_limits(symbol)
    if limits is None:
        return None
    if not isinstance(limits, Mapping):
        raise TypeError("data_view.get_limits must return a mapping or None")
    return limits


@dataclass(frozen=True, slots=True)
class ExecutionOrchestrator:
    """Orchestrate validation, clipping, venue execution, and settlement.

    Pipeline:
      targets (ideal) -> intents (delta) -> clip (institution) -> validate -> venue -> fills -> settlement

    Contract:
    - single entry point for institutional world
    - portfolio constructor never sees T+1/limits/cash problems

    Example:
        orchestrator = ExecutionOrchestrator(
            clip_policy=clip_policy,
            validator=validator,
            venue=venue,
            settlement=settlement,
        )
        fills = orchestrator.execute_targets(
            ts_us=1,
            targets={"600000": 100},
            data_view=view,
            state=state,
            ledger=ledger,
        )
    """
    clip_policy: AShareTargetClippingPolicy
    validator: AShareOrderValidation
    venue: SimImmediateVenue
    settlement: SettlementEngine

    def execute_targets(
        self,
        *,
        ts_us: int,
        targets: Mapping[str, int],
        data_view: MarketDataView,
        state: PortfolioState,
        ledger: ExecutionLedger,
        forced_close: bool = False,
        ignore_t1_forced_close: bool = True,
    ) -> list[FillEvent]:
        """Execute target positions against the supplied observable facts.

        Example:
            fills = orchestrator.execute_targets(
                ts_us=1,
                targets={"600000": 100},
                data_view=view,
                state=state,
                ledger=ledger,
            )
        """
        ts_us = int(ts_us)
        fills: list[FillEvent] = []

        intents: list[OrderIntent] = []
        available_cash = float(state.cash)
        for symbol, tgt in targets.items():
            sym = str(symbol)
            cur = int(state.positions.get(sym, 0))
            tgt_i = int(tgt)
            delta = tgt_i - cur
            if delta == 0:
                continue

            side = Side.BUY if delta > 0 else Side.SELL
            qty = abs(int(delta))
            if qty <= 0:
                continue

            order_id = ledger.next_order_id()

            ledger.record_order_submit(
                ts_us=ts_us,
                symbol=sym,
                side=side.value,
                qty=qty,
                order_id=order_id,
            )

            px = data_view.get_price(sym)
            if px is None or not isinstance(px, (int, float)) or not math.isfinite(float(px)) or float(px) <= 0.0:
                ledger.record_order_reject(
                    ts_us=ts_us,
                    symbol=sym,
                    side=side.value,
                    qty=qty,
                    order_id=order_id,
                    reason="INVALID_PRICE",
                )
                continue

            # collect institutional facts
            cur_pos = int(state.positions.get(sym, 0))
            t1_sellable = int(state.t1_sellable(sym))

            limits = _optional_limits(data_view, sym)

            # forced close (backtest only) may ignore T+1
            effective_t1 = t1_sellable
            if forced_close and ignore_t1_forced_close and side == Side.SELL:
                effective_t1 = max(effective_t1, cur_pos)

            # clip
            cr = self.clip_policy.clip_qty(
                side=side,
                requested_qty=qty,
                price=float(px),
                cash=available_cash if side == Side.BUY else float(state.cash),
                current_position=cur_pos,
                t1_sellable=effective_t1,
                limits=limits,
            )
            if cr.clipped_qty <= 0:
                ledger.record_order_reject(
                    ts_us=ts_us,
                    symbol=sym,
                    side=side.value,
                    qty=qty,
                    order_id=order_id,
                    reason=cr.reason or "CLIPPED_TO_ZERO",
                )
                continue

            intent = OrderIntent(
                ts_us=ts_us,
                symbol=sym,
                side=side,
                qty=int(cr.clipped_qty),
                order_id=order_id,
                meta={"clip_reason": cr.reason} if cr.reason else {},
                active_ts_us=ts_us,
                expires_ts_us=ts_us + 1,
                limit_price=None,
            )

            # validate
            vr = self.validator.validate(
                intent=intent,
                price=float(px),
                cash=available_cash if side == Side.BUY else float(state.cash),
                current_position=cur_pos,
                t1_sellable=effective_t1,
            )
            if not vr.ok:
                ledger.record_order_reject(
                    ts_us=ts_us,
                    symbol=sym,
                    side=side.value,
                    qty=int(intent.qty),
                    order_id=order_id,
                    reason=vr.reason,
                    meta={"detail": vr.detail} if vr.detail else None,
                )
                continue

            if side == Side.BUY:
                estimated_price = self.venue.slippage_model.apply(side=side.value, price=px)
                budgeted_qty = affordable_quantity(
                    order=intent, price=estimated_price, maximum=intent.qty,
                    step=self.validator.lot_size, cash=Decimal(str(available_cash)),
                    settlement=self.settlement, ledger=ledger,
                )
                if budgeted_qty <= 0:
                    ledger.record_order_reject(
                        ts_us=ts_us,
                        symbol=sym,
                        side=side.value,
                        qty=int(intent.qty),
                        order_id=order_id,
                        reason="INSUFFICIENT_CASH",
                    )
                    continue

                if budgeted_qty < int(intent.qty):
                    meta = dict(intent.meta)
                    meta["clip_reason"] = "INSUFFICIENT_CASH"
                    intent = replace(intent, qty=budgeted_qty, meta=meta)

                turnover = estimated_price * intent.qty
                cost = self.settlement.incremental_cost(
                    order_id=intent.order_id, side=side, symbol=sym,
                    turnover=turnover, ledger=ledger,
                )
                available_cash = float(Decimal(str(available_cash)) - turnover - cost.total)

            intents.append(intent)

        if not intents:
            return fills

        # inside execute_targets

        venue_fills = self.venue.execute(
            ts_us=ts_us,
            intents=intents,
            data_view=data_view,
            forced_close=forced_close,
        )

        for f in venue_fills:
            self.settlement.settle_fill(
                fill=f,
                portfolio=state,
                ledger=ledger,
            )

            fills.append(f)

        return fills
