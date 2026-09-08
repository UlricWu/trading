# filepath: src/trading/execution/engine.py
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from src.trading.core.events import OrderIntent, Side, FillEvent
from src.trading.core.ledger import ExecutionLedger
from src.trading.execution.models.cost_a_share import AShareCostModel
from src.trading.execution.policies.constraints import AShareTargetClippingPolicy
from src.trading.execution.policies.validation import AShareOrderValidation
from src.trading.execution.settlement import SettlementEngine
from src.trading.execution.venue.sim_immediate import SimImmediateVenue
from src.trading.market.data_view import MarketDataView
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


def _round_down_lot(qty: int, lot_size: int) -> int:
    lot = max(1, int(lot_size))
    qty = int(qty)
    if qty <= 0:
        return 0
    return (qty // lot) * lot


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
            cost_model=cost_model,
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
    cost_model: AShareCostModel
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
                budgeted_qty = self._budgeted_buy_qty(
                    symbol=sym,
                    requested_qty=int(intent.qty),
                    market_price=float(px),
                    available_cash=available_cash,
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
                    intent = OrderIntent(
                        ts_us=ts_us,
                        symbol=sym,
                        side=side,
                        qty=budgeted_qty,
                        order_id=order_id,
                        meta=meta,
                    )
                    vr = self.validator.validate(
                        intent=intent,
                        price=float(px),
                        cash=available_cash,
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

                required_cash = self._estimated_buy_cash_required(
                    symbol=sym,
                    qty=int(intent.qty),
                    market_price=float(px),
                )
                available_cash = max(0.0, available_cash - required_cash)

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
            mp = data_view.get_price(str(f.symbol))

            self.settlement.settle_fill(
                fill=f,
                market_price=float(mp) if mp is not None else float(f.price),
                portfolio=state,
                ledger=ledger,
            )

            fills.append(f)

        return fills

    def _estimated_buy_cash_required(
        self,
        *,
        symbol: str,
        qty: int,
        market_price: float,
    ) -> float:
        qty = int(qty)
        if qty <= 0:
            return 0.0
        exec_price = self.venue.slippage_model.apply(
            side=Side.BUY.value,
            price=float(market_price),
        )
        cost = self.cost_model.compute(
            side=Side.BUY.value,
            price=float(exec_price),
            qty=qty,
            symbol=str(symbol),
        )
        return float(exec_price) * qty + float(cost.total)

    def _budgeted_buy_qty(
        self,
        *,
        symbol: str,
        requested_qty: int,
        market_price: float,
        available_cash: float,
    ) -> int:
        requested_qty = int(requested_qty)
        if requested_qty <= 0 or float(available_cash) <= 0.0:
            return 0
        if (
            self._estimated_buy_cash_required(
                symbol=symbol,
                qty=requested_qty,
                market_price=market_price,
            )
            <= float(available_cash)
        ):
            return requested_qty

        lot_size = int(getattr(self.validator, "lot_size", 1))
        high_lots = requested_qty // max(1, lot_size)
        low = 0
        high = high_lots
        best = 0
        while low <= high:
            mid = (low + high) // 2
            qty = _round_down_lot(mid * lot_size, lot_size)
            required = self._estimated_buy_cash_required(
                symbol=symbol,
                qty=qty,
                market_price=market_price,
            )
            if required <= float(available_cash):
                best = qty
                low = mid + 1
            else:
                high = mid - 1
        return best
