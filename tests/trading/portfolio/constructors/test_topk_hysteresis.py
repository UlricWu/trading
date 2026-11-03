# filepath: tests/trading/portfolio/constructors/test_topk_hysteresis.py
from decimal import Decimal

import pytest

from src.trading.portfolio.constructors.topk_hysteresis import TopKHysteresisConstructor
from src.trading.portfolio.state import PortfolioState


@pytest.mark.parametrize("invalid_score", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("has_prior_target", [False, True])
def test_rejected_scores_do_not_advance_rebalance_state(invalid_score: float, has_prior_target: bool) -> None:
    constructor = TopKHysteresisConstructor(max_positions=1, entry_threshold=0.5,
        exit_threshold=0.3, rebalance_interval_minutes=5, target_quantity=100)
    account = PortfolioState(Decimal("1000"), Decimal("1000"))
    if has_prior_target:
        assert constructor.targets(ts_us=0, scores={"A": 0.8}, state=account) == {"A": 100}

    with pytest.raises(ValueError, match="finite"):
        constructor.targets(ts_us=300_000_000, scores={"B": invalid_score}, state=account)

    assert constructor.targets(ts_us=300_000_000, scores={"B": 0.9}, state=account) == {"B": 100}
