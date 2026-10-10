# filepath: tests/trading/backtest/test_components.py
from src.config.backtest_config import RiskConfig
from src.trading.backtest.components import build_risk
from src.trading.risk.base import RiskContext


def test_declared_risk_chain_observes_and_reduces_in_configured_order() -> None:
    risk = build_risk(RiskConfig.model_validate({'rules': [
        {'type': 'max_single_name', 'max_ratio': .4}, {'type': 'max_gross_exposure', 'max_gross_ratio': .5}]}))
    context = RiskContext(1, {'A': 10, 'B': 10}, 10000, 10000, 10000, {}, (), {'A': 100, 'B': 100})
    decision = risk.apply(target={'A': 1000, 'B': 1000}, ctx=context)
    assert decision.adjusted == {'A': 200, 'B': 200}
    assert [rule.rule for rule in decision.rules] == ['max_single_name', 'max_gross_exposure']
    assert all(rule.triggered for rule in decision.rules)
