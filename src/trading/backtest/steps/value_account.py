# filepath: src/trading/backtest/steps/value_account.py
from typing import cast

from src.trading.backtest.context import BacktestContext, BacktestState, PositionValuation, TickState
from src.trading.backtest.market.replay import ReplayMarket
from src.trading.backtest.corporate_actions import CorporateActionIssue
from src.trading.portfolio.engine import mark_to_market


class ValueAccountStep:
    """Value the current account, retaining initial capital and official closing samples.

    The same Step runs before decisions and after submission. Same-time equity is
    replaced by the final value, while initial equity and the existing peak survive.

    Example:
        context = ValueAccountStep().run(context)
    """

    def run(self, context: BacktestContext) -> BacktestContext:
        """Value declared source gaps diagnostically while requiring visible held prices.

        Example:
            context = step.run(context)
        """
        state = cast(BacktestState, context.state)
        market = cast(ReplayMarket, context.market)
        tick = cast(TickState, context.tick)
        ts_us, session = tick.ts_us, tick.session
        is_initial = state.initial_equity is None
        is_close = ts_us == session.close_ts_us
        held = {symbol for symbol, qty in state.portfolio_state.positions.items() if qty}
        origins = market.price_marks
        suspended = market.suspended_close_symbols.get(ts_us, frozenset())
        gaps = {issue.symbol for issue in market.daily_close_issues.get(ts_us, ())
                if issue.reason == "missing_daily_close"}
        unpriced = held - origins.keys()
        if unpriced:
            raise ValueError(f"holdings require a prior visible raw price at {session.trade_date}/{ts_us}: {sorted(unpriced)}")
        if is_close:
            close_symbols = market.daily_close_symbols.get(ts_us, set())
            missing = held - close_symbols - suspended - gaps
            if missing:
                raise ValueError(f"end-of-day holdings require declared raw daily-close marks or confirmed suspension: {sorted(missing)}")
            invalid = {symbol for symbol in held & suspended if symbol not in origins
                       or origins[symbol].kind not in ("daily_close", "corporate_action")
                       or (origins[symbol].kind == "daily_close" and origins[symbol].trade_date >= session.trade_date)}
            if invalid:
                raise ValueError(f"suspended holdings require a prior visible raw daily close: {sorted(invalid)}")
        prices = {symbol: float(price) for symbol, price in market.prices.items()}
        valuation = mark_to_market(positions={symbol: state.portfolio_state.positions[symbol] for symbol in held},
            current_raw_prices=prices, last_mark_prices=state.last_mark_prices)
        state.last_mark_prices = valuation.mark_prices
        shares_receivable_value = 0.0
        for entitlement in state.portfolio_state.distribution_entitlements.values():
            action = entitlement.distribution
            if action.kind != "compensation" or not entitlement.recognized or entitlement.shares_credited:
                continue
            if not entitlement.shares_qty:
                continue
            mark = origins.get(action.symbol)
            if mark is None or mark.ts_us <= action.record_ts_us:
                key = (action.source_ref, action.action_id, "missing_compensation_mark")
                state.corporate_action_issues.setdefault(key, CorporateActionIssue(ts_us=ts_us,
                    symbol=action.symbol, action_id=action.action_id, reason=key[2], source_ref=action.source_ref))
            if mark is not None:
                shares_receivable_value += entitlement.shares_qty * float(mark.price)
        if is_initial:
            state.initial_equity = (float(state.portfolio_state.cash + state.portfolio_state.dividend_receivable)
                + valuation.market_value + shares_receivable_value)
            state.equity_curve.starting_peak_equity = max(state.equity_curve.starting_peak_equity, state.initial_equity)
        state.equity_curve.append(ts_us=ts_us, cash=float(state.portfolio_state.cash), market_value=valuation.market_value,
                                 dividend_receivable=float(state.portfolio_state.dividend_receivable),
                                 shares_receivable_value=shares_receivable_value)
        for phase in (("initial",) if is_initial else ()) + (("close",) if is_close else ()):
            state.valuation_marks[(ts_us, phase)] = tuple(
                PositionValuation(
                    ts_us, session.trade_date, symbol, state.portfolio_state.positions[symbol],
                    origins[symbol].price, origins[symbol].ts_us, origins[symbol].trade_date, origins[symbol].kind,
                    "initial" if phase == "initial" else "suspension" if symbol in suspended
                    else "missing_daily_close" if symbol in gaps else "daily_close",
                ) for symbol in sorted(held)
            )
        if is_close:
            state.daily_equity[session.trade_date] = state.equity_curve.points[-1]
        return context
