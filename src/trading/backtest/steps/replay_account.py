# filepath: src/trading/backtest/steps/replay_account.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from time import monotonic
from typing import cast

from src import logs
from src.config.backtest_config import FakeExecutionConfig, FixedHoldingStrategyConfig, InitialAccount, RiskConfig, StrategyConfig
from src.trading.backtest.clock import ReplayClock
from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestContext, BacktestInputs, BacktestState
from src.trading.backtest.execution.fill_models import ImmediateQuoteFillModel, LimitPriceFillModel
from src.trading.backtest.execution.models.cost_a_share import AShareCostModel
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.backtest.execution.settlement import SettlementEngine
from src.trading.backtest.execution.simulator import AccountExecutionSimulator
from src.trading.backtest.market.replay import ReplayMarket, ReplaySession
from src.trading.backtest.market.signals import ReplaySignal
from src.trading.core.equity import EquityCurve
from src.trading.core.events import OrderIntent, OrderTerms, Side, TargetEvent
from src.trading.core.time import advance_trading_minutes
from src.trading.portfolio.constructors.base import PortfolioConstructor
from src.trading.portfolio.constructors.factory import build_portfolio_constructor
from src.trading.portfolio.constructors.fixed_holding import FixedHoldingConstructor
from src.trading.portfolio.constructors.topk_hysteresis import TopKHysteresisConstructor
from src.trading.portfolio.engine import make_executable_targets, mark_to_market
from src.trading.portfolio.state import PortfolioState
from src.trading.risk import RiskManager
from src.trading.risk.base import RiskContext
from src.trading.risk.evaluation import RiskCase, RiskObservation
from src.utils.datetime_utils import DateTimeUtils


@dataclass(frozen=True, slots=True)
class ReplayAccountStep:
    """Own the account, strategy and clock for one complete causal replay.

    Example:
        step = ReplayAccountStep(initial_account=initial, execution=execution,
            strategy=strategy, risk=None, prediction_dir=prediction_dir)
        context = step.run(context)
    """

    initial_account: InitialAccount
    execution: FakeExecutionConfig
    strategy: StrategyConfig | None
    risk: RiskConfig | None
    prediction_dir: Path
    progress_clock: Callable[[], float] = monotonic

    def run(self, context: BacktestContext) -> BacktestContext:
        """Settle old orders, decide, submit and value at each visible clock tick.

        Example:
            context = step.run(context)
        """
        inputs = cast(BacktestInputs, context.inputs)
        initial = self.initial_account
        config = self.execution
        account = PortfolioState(
            initial_cash=initial.cash, cash=initial.cash,
            positions=initial.positions, avg_cost=initial.average_cost,
            buy_qty_today={s: qty - initial.sellable[s] for s, qty in initial.positions.items()},
            current_day=initial.trade_date,
        )
        state = BacktestState(account, equity_curve=EquityCurve(starting_peak_equity=initial.peak_equity))
        market = ReplayMarket(inputs.market)
        fill_model = (ImmediateQuoteFillModel() if config.fill.type == "quoted"
                      else LimitPriceFillModel(FixedBPSlippageModel(config.fill.slippage_bp)))
        fees = config.fees
        simulator = AccountExecutionSimulator(
            portfolio=account, ledger=state.execution_ledger, pending_orders=state.pending_orders,
            settlement=SettlementEngine(cost_model=AShareCostModel(
                commission_rate=fees.commission_rate, stamp_tax_rate=fees.stamp_tax_rate,
                transfer_rate=fees.transfer_rate, min_commission=fees.min_commission,
            )),
            fill_model=fill_model, rules=config.quantity_rules,
            quote_rule=self.strategy.quote if self.strategy is not None else None,
        )
        source = (inputs.decisions if isinstance(inputs.decisions, OrderCommandSchedule)
                  else ReplaySignal(inputs.decisions, self.prediction_dir))
        clock = ReplayClock.from_iterable(
            (*inputs.market.timestamps(), *source.timestamps(), *config.risk_check_ts_us,
             initial.ts_us, config.end_ts_us),
            start_us=initial.ts_us, end_us=config.end_ts_us,
        )
        constructor: PortfolioConstructor | FixedHoldingConstructor | None = None
        if isinstance(source, ReplaySignal):
            strategy = cast(StrategyConfig, self.strategy)
            if isinstance(strategy, FixedHoldingStrategyConfig):
                params = strategy.params
                if params.initial_exit_dates.keys() != {s for s, qty in initial.positions.items() if qty}:
                    raise ValueError("fixed_holding initial positions require explicit exit dates")
                deadlines = {
                    symbol: DateTimeUtils.local_time_to_utc_epoch_us(params.exit_time, date.fromisoformat(day))
                    for symbol, day in params.initial_exit_dates.items()
                }
                for deadline in deadlines.values():
                    inputs.market.session_at(deadline)
                    if deadline < initial.ts_us:
                        raise ValueError("initial exit plan precedes account snapshot")
                constructor = FixedHoldingConstructor(
                    max_positions=params.max_positions, decision_time=params.decision_time,
                    exit_time=params.exit_time, open_dates=tuple(s.trade_date for s in inputs.market.sessions),
                    rules=config.quantity_rules, exit_due=deadlines,
                )
                for deadline in deadlines.values():
                    clock.schedule(ts_us=deadline)
            else:
                constructor = build_portfolio_constructor(strategy)
        else:
            context.metrics.not_run.update(signal="frozen_commands", strategy="frozen_commands")
        context.metrics.applied_risk = self.risk
        if self.risk is None:
            context.metrics.not_run["risk"] = "no_business_risk"
        if config.fill.type == "quoted":
            context.metrics.not_run.update(price_feasibility="quoted_fills", market_liquidity="quoted_fills")
        risk = RiskManager.from_config(self.risk) if self.risk is not None else None
        quantity_steps = {symbol: rule.buy_step_qty for symbol, rule in config.quantity_rules.items()}
        windows = tuple(window for session in inputs.market.sessions for window in session.windows)
        last_progress = self.progress_clock()

        for count, ts_us in enumerate(clock, 1):
            session = inputs.market.session_at(ts_us)
            try:
                market.on_time(ts_us)
                account.on_trading_day_start(session.trade_date)
                simulator.advance(ts_us=ts_us, observations=market.observations_at(ts_us))
                _record_valuation(state=state, market=market, session=session, ts_us=ts_us)
                wakeups: set[int] = set()
                if isinstance(source, OrderCommandSchedule):
                    for command in source.at(ts_us):
                        if isinstance(command, OrderIntent):
                            simulator.submit_orders(orders=(command,))
                        else:
                            simulator.cancel_orders(ts_us=command.ts_us, order_ids=command.order_ids)
                else:
                    batch = source.at(ts_us)
                    if batch is not None:
                        state.signal_tape.append(batch.event)
                        state.prediction_refs.append(batch.prediction_ref)
                        state.decision_dates[ts_us] = batch.trade_date
                    target: TargetEvent | None = None
                    if isinstance(constructor, FixedHoldingConstructor):
                        target = constructor.targets(
                            ts_us=ts_us, trade_date=session.trade_date,
                            scores=batch.event.scores if batch is not None else None,
                            state=account, prices=market.prices, pending_orders=tuple(state.pending_orders.values()),
                        )
                        wakeups.update(constructor.wakeups())
                    elif batch is not None:
                        raw = cast(PortfolioConstructor, constructor).targets(
                            ts_us=ts_us, scores=batch.event.scores, state=account,
                        )
                        targets = make_executable_targets(
                            raw_targets=raw, positions=account.positions,
                            current_raw_prices={s: float(p) for s, p in market.prices.items()},
                            hold_symbols=set(batch.symbols) - batch.event.scores.keys(),
                            pending_orders=tuple(state.pending_orders.values()),
                            max_positions=constructor.max_positions if isinstance(constructor, TopKHysteresisConstructor) else None,
                        )
                        target = TargetEvent(ts_us, targets)
                    if target is not None:
                        state.target_tape.append(target)
                    cancel_order_ids: tuple[int, ...] = ()
                    if risk is not None:
                        equity = state.equity_curve.points[-1]
                        case = RiskCase(str(ts_us), session.trade_date,
                            target.targets if target is not None else None,
                            RiskContext(ts_us, {s: float(p) for s, p in market.prices.items()},
                                equity.equity, float(account.cash), equity.peak_equity, account.positions,
                                tuple(state.pending_orders.values()), quantity_steps))
                        decision = risk.apply(target=case.target, ctx=case.context)
                        state.risk_observations.append(RiskObservation(case, decision))
                        target = TargetEvent(ts_us, decision.adjusted) if decision.adjusted is not None else None
                        cancel_order_ids = decision.cancel_order_ids
                    if target is not None:
                        new_symbols = []
                        for symbol, quantity in target.targets.items():
                            delta = quantity - account.positions.get(symbol, 0)
                            side = Side.BUY if delta > 0 else Side.SELL
                            covered = sum(order.remaining_qty for order in state.pending_orders.values()
                                          if order.intent.symbol == symbol and order.intent.side == side)
                            if abs(delta) > covered:
                                new_symbols.append(symbol)
                        terms = {}
                        if new_symbols:
                            expiry = advance_trading_minutes(start_ts_us=ts_us,
                                minutes=cast(int, cast(StrategyConfig, self.strategy).order_window_minutes), windows=windows)
                            terms = {symbol: OrderTerms(ts_us, expiry) for symbol in new_symbols}
                        simulator.submit_targets(target=target, cancel_order_ids=cancel_order_ids,
                                                 terms=terms, market=market)
                    elif cancel_order_ids:
                        simulator.cancel_orders(ts_us=ts_us, order_ids=cancel_order_ids)
                wakeups.update(order.intent.expires_ts_us for order in state.pending_orders.values())
                _record_valuation(state=state, market=market, session=session, ts_us=ts_us)
                if risk is not None:
                    observation = state.risk_observations[-1]
                    equity = state.equity_curve.points[-1]
                    outcome = replace(observation.case.context,
                        equity=equity.equity, cash=float(account.cash), peak_equity=equity.peak_equity,
                        positions=account.positions, pending_orders=tuple(state.pending_orders.values()))
                    state.risk_observations[-1] = replace(observation, outcome=outcome)
                for wakeup in sorted(wakeups):
                    clock.schedule(ts_us=wakeup)
            except Exception as exc:
                exc.add_note(f"回测时点; trade_date={session.trade_date} ts_us={ts_us}")
                raise
            now = self.progress_clock()
            if now - last_progress >= 5 and len(clock):
                logs.info(f"⏳ 回测推进; trade_date={session.trade_date} ts_us={ts_us} processed={count}")
                last_progress = now
        context.state = state
        return context


def _record_valuation(*, state: BacktestState, market: ReplayMarket, session: ReplaySession, ts_us: int) -> None:
    """Keep initial capital separate while replacing the final value at the same tick."""
    prices = {symbol: float(price) for symbol, price in market.prices.items()}
    valuation = mark_to_market(positions=state.portfolio_state.positions,
        current_raw_prices=prices, last_mark_prices=state.last_mark_prices)
    state.last_mark_prices = valuation.mark_prices
    if state.initial_equity is None:
        state.initial_equity = float(state.portfolio_state.cash) + valuation.market_value
        state.equity_curve.starting_peak_equity = max(state.equity_curve.starting_peak_equity, state.initial_equity)
    state.equity_curve.append(ts_us=ts_us, cash=float(state.portfolio_state.cash), market_value=valuation.market_value)
    if ts_us == session.close_ts_us:
        close_symbols = market.daily_close_symbols.get(session.close_ts_us, set())
        if not state.portfolio_state.positions.keys() <= close_symbols:
            raise ValueError("end-of-day holdings require declared raw daily-close marks")
        state.daily_equity[session.trade_date] = state.equity_curve.points[-1]
