# filepath: tests/training/test_evaluation.py
import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from src.training.evaluation import evaluate_predictions, load_evaluation, save_evaluation, summarize_observations


def test_missing_counts_are_mutually_exclusive_and_zero_is_a_prediction(tmp_path: Path) -> None:
    observation = evaluate_predictions(
        model_ref=tmp_path / "model.joblib", decision_ts_us=123,
        predictions=np.array([0.0, np.nan, 2.0, 3.0]), labels=np.array([1.0, np.nan, np.nan, 3.0]),
    )
    assert (observation.input_count, observation.no_prediction_count, observation.missing_label_count, observation.pair_count) == (4, 1, 1, 2)
    assert observation.value == 1.0


def test_json_retains_all_dates_null_reasons_and_coverage(tmp_path: Path) -> None:
    observations = tuple(evaluate_predictions(
        model_ref=tmp_path / f"{index}.joblib", decision_ts_us=index,
        predictions=np.array(x), labels=np.array(y),
    ) for index, (x, y) in enumerate([
        ([0.0, 1.0], [1.0, 2.0]), ([], []), ([1.0], [2.0]),
    ]))
    output = tmp_path / "metrics.json"
    save_evaluation(output, observations)
    payload = json.loads(output.read_text())
    assert payload["observations"][1]["value"] is None
    assert payload["observations"][2]["undefined_reasons"] == ["insufficient_pairs", "constant_prediction", "constant_label"]
    assert payload["summary"] == {"date_count": 3, "defined_date_count": 1, "mean_ic": 1.0, "undefined_reason_counts": {"constant_label": 1, "constant_prediction": 1, "insufficient_pairs": 2}}
    assert load_evaluation(output).observations == observations
    assert "NaN" not in output.read_text()
    assert summarize_observations(observations[1:]).mean_ic is None


def test_persisted_nonfinite_metric_is_rejected(tmp_path: Path) -> None:
    output = tmp_path / "metrics.json"
    save_evaluation(output, ())
    payload = json.loads(output.read_text())
    payload["summary"]["mean_ic"] = float("nan")
    output.write_text(json.dumps(payload))
    with pytest.raises(ValidationError):
        load_evaluation(output)


@pytest.mark.parametrize("mutation", ["counts", "value_reason", "summary"])
def test_persisted_metrics_must_have_consistent_counts_nulls_and_summary(tmp_path: Path, mutation: str) -> None:
    output = tmp_path / "metrics.json"
    observation = evaluate_predictions(model_ref=tmp_path / "model.joblib", decision_ts_us=1, predictions=np.array([1.0]), labels=np.array([2.0]))
    save_evaluation(output, (observation,))
    payload = json.loads(output.read_text())
    if mutation == "counts":
        payload["observations"][0]["input_count"] = 2
    elif mutation == "value_reason":
        payload["observations"][0]["value"] = 0.0
    else:
        payload["summary"]["mean_ic"] = 0.0
    output.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_evaluation(output)
