# filepath: tests/trading/risk/test_evaluation.py
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from src.trading.risk.base import RiskContext
from src.trading.risk.engine import RiskManager
from src.trading.risk.evaluation import RiskCase, evaluate_risk_cases, read_risk_cases
from src.trading.risk.rules.max_position import MaxSingleNameNotionalRule


def test_frozen_cases_reproduce_allow_reduce_reject_without_account_mutation(tmp_path: Path) -> None:
    context = RiskContext(1, {'A': 10}, 10000, 10000, 10000, {}, (), {'A': 100})
    cases = (RiskCase('allow', '2026-07-03', {'A': 100}, context),
             RiskCase('reduce', '2026-07-03', {'A': 1000}, context))
    ref = tmp_path / 'risk-cases.json'
    ref.write_bytes(TypeAdapter(tuple[RiskCase, ...]).dump_json(cases, warnings='error'))
    loaded = read_risk_cases((ref,))
    expected = evaluate_risk_cases(cases, risk=RiskManager((MaxSingleNameNotionalRule(.2),)))
    assert evaluate_risk_cases(loaded, risk=RiskManager((MaxSingleNameNotionalRule(.2),))) == expected
    assert [item.decision.reason for item in expected] == ['allowed', 'adjusted']
    rejected = evaluate_risk_cases(loaded, risk=RiskManager((MaxSingleNameNotionalRule(0),)))
    assert all(item.decision.reason == 'rejected' and item.decision.adjusted is None for item in rejected)
    assert context.positions == {}
    with pytest.raises(ValueError, match='unique'):
        read_risk_cases((ref, ref))
    payload = json.loads(ref.read_text())
    payload[0]['target']['A'] = True
    ref.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        read_risk_cases((ref,))
