# filepath: src/trading/backtest/execution/fill_models.py
from dataclasses import dataclass
from decimal import Decimal

from src.trading.core.events import OrderIntent, Side
from src.trading.backtest.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.backtest.market.data_view import ExecutionObservation


@dataclass(frozen=True, slots=True)
class PriceMatch:
    """Candidate price and the quote test, including unsuccessful tests.

    Example:
        match = PriceMatch(Decimal("10.01"), False)
    """

    candidate_price: Decimal
    matches: bool


@dataclass(frozen=True, slots=True)
class ImmediateQuoteFillModel:
    """Assume full fills at each accepted order's quote when it becomes active.

    Example:
        fill_model = ImmediateQuoteFillModel()
    """


@dataclass(frozen=True, slots=True)
class LimitPriceFillModel:
    """Test the explicitly slipped observation price against the frozen quote.

    Example:
        fill_model = LimitPriceFillModel(slippage_model=FixedBPSlippageModel(bp=10))
        match = fill_model.match(order=order, observation=observation)
    """

    slippage_model: FixedBPSlippageModel

    def match(self, *, order: OrderIntent, observation: ExecutionObservation) -> PriceMatch:
        """Retain an out-of-limit candidate price, without clamping it to the quote.

        Example:
            match = fill_model.match(order=order, observation=observation)
        """
        candidate = self.slippage_model.apply(side=order.side.value, price=observation.price)
        limit_price = order.limit_price
        matches = candidate <= limit_price if order.side == Side.BUY else candidate >= limit_price
        return PriceMatch(candidate, matches)
