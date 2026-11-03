# filepath: tests/trading/risk/test_evaluation.py
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter

from src.trading.risk.base import RiskContext
from src.trading.risk.engine import RiskManager
from src.trading.risk.evaluation import RiskCase, RiskCases
from src.trading.risk.rules.max_position import MaxSingleNameNotionalRule


def test_frozen_cases_reproduce_allow_reduce_reject_without_account_mutation(tmp_path: Path) -> None:
    context = RiskContext(1, {'A': 10}, 10000, 10000, 10000, {}, (), {'A': 100})
    cases = (RiskCase('allow', '2026-07-03', {'A': 100}, context),
             RiskCase('reduce', '2026-07-03', {'A': 1000}, context))
    ref = tmp_path / 'risk-cases.json'
    ref.write_bytes(TypeAdapter(tuple[RiskCase, ...]).dump_json(cases, warnings='error'))
    loaded = RiskCases.from_files((ref,))
    expected = RiskCases(cases).evaluate(risk=RiskManager((MaxSingleNameNotionalRule(.2),)))
    assert loaded.evaluate(risk=RiskManager((MaxSingleNameNotionalRule(.2),))) == expected
    assert [item.decision.reason for item in expected] == ['allowed', 'adjusted']
    rejected = loaded.evaluate(risk=RiskManager((MaxSingleNameNotionalRule(0),)))
    assert all(item.decision.reason == 'rejected' and item.decision.adjusted is None for item in rejected)
    assert context.positions == {}
    with pytest.raises(ValueError, match='unique'):
        RiskCases.from_files((ref, ref))
    payload = json.loads(ref.read_text())
    payload[0]['target']['A'] = True
    ref.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        RiskCases.from_files((ref,))


@pytest.fixture
def case() -> RiskCase:
    return RiskCase('case-1', '2026-07-03', {'A': 1000},
                    RiskContext(1, {'A': 10}, 10000, 10000, 10000, {}, (), {'A': 100}))


def test_collection_owns_an_immutable_copy_without_reordering_cases(case: RiskCase) -> None:
    earlier = replace(case, case_id='case-0')
    source = [case, earlier]
    cases = RiskCases(source)
    source.clear()

    assert cases.cases == (case, earlier)
    with pytest.raises(FrozenInstanceError):
        setattr(cases, 'cases', ())


def test_constructor_rejects_duplicate_case_ids(case: RiskCase) -> None:
    with pytest.raises(ValueError, match='case_id must be unique'):
        RiskCases((case, replace(case, target={'A': 100})))


def test_files_preserve_case_order_and_the_existing_array_format(tmp_path: Path, case: RiskCase) -> None:
    timer = replace(case, case_id='timer', target=None)
    first_ref, second_ref, combined_ref = (tmp_path / name for name in ('first.json', 'second.json', 'combined.json'))
    RiskCases((case,)).write(first_ref)
    RiskCases((timer,)).write(second_ref)

    loaded = RiskCases.from_files((second_ref, first_ref))
    loaded.write(combined_ref)

    assert loaded.cases == (timer, case)
    assert combined_ref.read_bytes() == TypeAdapter(tuple[RiskCase, ...]).dump_json((timer, case), warnings='error')
    assert [item['case_id'] for item in json.loads(combined_ref.read_bytes())] == ['timer', 'case-1']
    observations = loaded.evaluate(risk=RiskManager((MaxSingleNameNotionalRule(.2),)))
    assert [item.case for item in observations] == [timer, case]
    assert [item.decision.reason for item in observations] == ['no_action', 'adjusted']
    assert observations[1].decision.adjusted == {'A': 200}


def test_case_ids_are_unique_across_distinct_files(tmp_path: Path, case: RiskCase) -> None:
    first_ref, second_ref = tmp_path / 'first.json', tmp_path / 'second.json'
    RiskCases((case,)).write(first_ref)
    RiskCases((replace(case, target=None),)).write(second_ref)

    with pytest.raises(ValueError, match='case_id must be unique'):
        RiskCases.from_files((first_ref, second_ref))


def test_empty_collection_round_trips_and_has_no_observations(tmp_path: Path) -> None:
    cases = RiskCases.from_files(())
    ref = tmp_path / 'empty.json'
    cases.write(ref)

    assert ref.read_bytes() == b'[]'
    assert RiskCases.from_files((ref,)) == cases
    assert cases.evaluate(risk=RiskManager((MaxSingleNameNotionalRule(.2),))) == ()


def test_file_errors_propagate_without_producing_cases(tmp_path: Path) -> None:
    ref = tmp_path / 'cases.json'
    with pytest.raises(FileNotFoundError):
        RiskCases.from_files((ref,))
    ref.write_bytes(b'{')
    with pytest.raises(ValueError):
        RiskCases.from_files((ref,))


def test_failed_write_preserves_the_previous_file(
    tmp_path: Path, case: RiskCase, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = tmp_path / 'cases.json'
    ref.write_bytes(b'previous cases')
    failure = OSError('publication failed')
    monkeypatch.setattr('src.utils.filesystem.os.replace', Mock(side_effect=failure))

    with pytest.raises(OSError) as raised:
        RiskCases((case,)).write(ref)

    assert raised.value is failure
    assert ref.read_bytes() == b'previous cases'
    assert tuple(tmp_path.iterdir()) == (ref,)


def test_evaluation_propagates_risk_failure_without_mutating_cases(case: RiskCase) -> None:
    missing_prices = replace(case, context=replace(case.context, prices={}))
    cases = RiskCases((missing_prices,))

    with pytest.raises(ValueError, match='requires prices'):
        cases.evaluate(risk=RiskManager((MaxSingleNameNotionalRule(.2),)))

    assert cases.cases == (missing_prices,)
    assert missing_prices.target == {'A': 1000}
