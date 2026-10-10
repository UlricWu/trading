# filepath: src/trading/execution/venue/fake.py
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, cast

from src.trading.core.events import OrderIntent, Side
from src.trading.execution.models.slippage_fixed_bp import FixedBPSlippageModel
from src.trading.market.data_view import ExecutionObservation


@dataclass(frozen=True, slots=True)
class PriceMatch:
    """Candidate price and the quote test, including unsuccessful tests.

    Example:
        match = PriceMatch(Decimal("10.01"), False)
    """

    candidate_price: Decimal
    matches: bool


@dataclass(frozen=True, slots=True)
class ReferenceVenue:
    """Use subsequent reference prices without quote or market-volume tests.

    Example:
        venue = ReferenceVenue()
        match = venue.match(order=order, observation=observation)
    """

    requires_quote: ClassVar[bool] = False

    def match(self, *, order: OrderIntent, observation: ExecutionObservation) -> PriceMatch:
        """Assign the declared reference price; resources are handled separately.

        Example:
            match = venue.match(order=order, observation=observation)
        """
        return PriceMatch(observation.price, True)


@dataclass(frozen=True, slots=True)
class LimitPriceVenue:
    """Test the explicitly slipped observation price against the frozen quote.

    Example:
        venue = LimitPriceVenue(slippage_model=FixedBPSlippageModel(bp=10))
        match = venue.match(order=order, observation=observation)
    """

    slippage_model: FixedBPSlippageModel
    requires_quote: ClassVar[bool] = True

    def match(self, *, order: OrderIntent, observation: ExecutionObservation) -> PriceMatch:
        """Retain an out-of-limit candidate price, without clamping it to the quote.

        Example:
            match = venue.match(order=order, observation=observation)
        """
        candidate = self.slippage_model.apply(side=order.side.value, price=observation.price)
        limit_price = cast(Decimal, order.limit_price)
        matches = candidate <= limit_price if order.side == Side.BUY else candidate >= limit_price
        return PriceMatch(candidate, matches)
