# filepath: src/trading/backtest/steps/prepare_inputs.py
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from src.access import Access
from src.jobs.requests import BacktestSubmission, ExecutionEvalSubmission
from src.trading.backtest.commands import OrderCommandSchedule
from src.trading.backtest.context import BacktestContext, BacktestInputs
from src.trading.backtest.market.replay import ReplayMarketInput
from src.trading.backtest.market.signals import prepare_signals
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class PrepareInputsStep:
    """Combine the prepared market with validated account coverage and decision inputs.

    Example:
        context = PrepareInputsStep(submission=submission, access=access, pm=pm).run(context)
    """

    submission: BacktestSubmission
    access: Access
    pm: PathManager

    def run(self, context: BacktestContext) -> BacktestContext:
        """Consume in-memory market facts and prepare decisions without predicting or reading labels.

        Example:
            context = step.run(context)
        """
        submission = self.submission
        config = submission.execution
        initial = submission.initial_account
        source = cast(ReplayMarketInput, context.market_input)
        if config.market_source.symbols and not initial.positions.keys() <= set(config.market_source.symbols):
            raise ValueError("market_source.symbols must cover initial holdings")
        if source.session_at(initial.ts_us).trade_date != initial.trade_date:
            raise ValueError("initial snapshot trade_date disagrees with replay start")
        source.session_at(config.end_ts_us)
        if config.fill.type == "quoted" and any(
            mark.data_issues and mark.ts_us <= config.end_ts_us for mark in source.marks
        ):
            raise ValueError("diagnostic replay with unresolved market data issues requires limit_price execution")
        for ts_us in config.risk_check_ts_us:
            source.session_at(ts_us)
            if not initial.ts_us <= ts_us <= config.end_ts_us:
                raise ValueError("risk check is outside the simulation interval")
        if config.fill.type == "limit_price" and any(o.volume_qty is None for o in source.observations):
            raise ValueError("price-model execution requires explicit observed volume_qty")
        if isinstance(submission, ExecutionEvalSubmission):
            decisions = OrderCommandSchedule.from_files(
                submission.order_refs, start=submission.start, end=submission.end, market=source,
                start_ts_us=initial.ts_us, end_ts_us=config.end_ts_us,
            )
        else:
            decisions = prepare_signals(
                submission.signal, start=submission.start, end=submission.end,
                access=self.access, pm=self.pm, min_listing_calendar_days=submission.min_listing_calendar_days,
                start_ts_us=initial.ts_us, end_ts_us=config.end_ts_us,
            )
        context.inputs = BacktestInputs(market=source, decisions=decisions)
        return context
