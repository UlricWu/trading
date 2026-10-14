# filepath: tests/jobs/test_requests.py
"""Submission construction and CLI command contract tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from src.config.backtest_config import BacktestMode
from src.jobs.requests import (
    DataSubmission,
    FeatureBackfillSubmission,
    InvalidJobRequest,
    Level2MinuteBackfillSubmission,
    StandardFactBootstrapSubmission,
    Stock1430BackfillSubmission,
    Stock1430FusionBackfillSubmission,
    TrainingSubmission,
    build_cli_command,
    create_feature_backfill_submission,
    create_level2_minute_backfill_submission,
    create_standard_fact_bootstrap_submission,
    create_stock_1430_backfill_submission,
    create_stock_1430_fusion_backfill_submission,
    parse_job_request,
)



def _training_request() -> dict[str, object]:
    return {
        "kind": "train",
        "train_range": {"start": "2026-07-01", "end": "2026-07-20"},
        "model": "sgd_regression",
        "dataset": {
            "feature_group": "daily_basic_selected_v1",
            "label_group": "daily_close_return_rank_d1/v1",
        },
    }

def test_data_range_creates_one_atomic_submission() -> None:
    submissions = parse_job_request(
        {
            "kind": "data-standard",
            "start": "2026-07-18",
            "end": "2026-07-20",
        }
    )

    assert submissions == [
        DataSubmission(
            kind="data-standard",
            start="2026-07-18",
            end="2026-07-20",
        ),
    ]


def test_training_range_creates_one_submission() -> None:
    submissions = parse_job_request(
        {
            **_training_request(),
        }
    )

    assert len(submissions) == 1 and isinstance(submissions[0], TrainingSubmission)
    assert submissions[0].start == "2026-07-01" and submissions[0].end == "2026-07-20"
    assert submissions[0].train_range.model_dump() == {"start": "2026-07-01", "end": "2026-07-20"}
    command = build_cli_command(submissions[0], "run-1")
    assert command[command.index("--train-start") + 1] == "2026-07-01"
    assert command[command.index("--train-end") + 1] == "2026-07-20"
    assert command[command.index("--model") + 1] == "sgd_regression"
    assert command[command.index("--feature-group") + 1] == "daily_basic_selected_v1"
    assert command[command.index("--label-group") + 1] == "daily_close_return_rank_d1/v1"
    assert "--eval-start" not in command and "--windows-json" not in command
    assert command[command.index("--experiment-id") + 1] == "run-1"


def test_cli_only_data_ranges_create_distinct_submissions() -> None:
    assert create_standard_fact_bootstrap_submission(
        "2019-01-01",
        "2019-04-03",
    ) == StandardFactBootstrapSubmission(
        start="2019-01-01",
        end="2019-04-03",
    )
    assert create_feature_backfill_submission(
        feature_set="tushare_daily_basic",
        version="v1",
        start="2019-04-04",
        end="2019-07-05",
    ) == FeatureBackfillSubmission(
        feature_set="tushare_daily_basic",
        version="v1",
        start="2019-04-04",
        end="2019-07-05",
    )
    assert create_level2_minute_backfill_submission(
        "2025-11-18",
        "2025-11-19",
    ) == Level2MinuteBackfillSubmission(
        start="2025-11-18",
        end="2025-11-19",
    )
    assert create_stock_1430_backfill_submission(
        "2026-05-06",
        "2026-05-07",
    ) == Stock1430BackfillSubmission(
        start="2026-05-06",
        end="2026-05-07",
    )


@pytest.mark.parametrize(
    "kind",
    [
        "data-calendar",
        "data-standard-bootstrap",
        "data-feature-backfill",
        "data-level2-minute-backfill",
        "data-stock-1430-backfill",
        "data-stock-1430-fusion-backfill",
    ],
)
def test_cli_only_data_kinds_are_not_http_job_kinds(kind: str) -> None:
    with pytest.raises(InvalidJobRequest, match="not supported"):
        parse_job_request(
            {
                "kind": kind,
                "start": "2019-01-01",
                "end": "2019-07-01",
            }
        )


def test_fusion_backfill_constructs_a_distinct_submission() -> None:
    assert create_stock_1430_fusion_backfill_submission(
        "2026-05-06", "2026-05-07"
    ) == Stock1430FusionBackfillSubmission(start="2026-05-06", end="2026-05-07")


@pytest.mark.parametrize(
    ("start", "end", "field"),
    (
        ("2026-5-6", "2026-05-07", "start"),
        ("2026-05-06", None, "end"),
        ("2026-05-07", "2026-05-06", "start"),
    ),
)
def test_fusion_backfill_rejects_invalid_ranges(
    start: object, end: object, field: str
) -> None:
    with pytest.raises(InvalidJobRequest) as caught:
        create_stock_1430_fusion_backfill_submission(start, end)
    assert caught.value.field == field


@pytest.mark.parametrize("mode", list(BacktestMode))
def test_backtest_command_preserves_mode_and_uses_job_id_as_experiment_id(
    mode: BacktestMode, backtest_payload: Callable[[BacktestMode], dict[str, object]],
) -> None:
    submission = parse_job_request(backtest_payload(mode))[0]
    assert submission.mode is mode
    command = build_cli_command(submission, "run-1", python_executable=Path("/candidate/python"))
    assert command[:4] == ("/candidate/python", "-m", "src.cli", "backtest")
    assert command[command.index("--experiment-id") + 1] == "run-1"
    assert command[command.index("--mode") + 1] == mode.value
    fields = json.loads(command[command.index("--input-json") + 1])
    assert parse_job_request({"kind": "backtest", "mode": mode.value, **fields}) == [submission]
    if mode == BacktestMode.RISK_RULE_EVAL:
        assert "start" in fields and "signal" in fields and "risk" in fields
    if mode == BacktestMode.SIGNAL_EVAL:
        assert "strategy" in fields and "initial_account" in fields


@pytest.mark.parametrize("source", ["model", "recorded"])
@pytest.mark.parametrize("slippage_bp", [0, 10])
def test_strategy_evaluation_accepts_price_model_and_preserves_cli_parameters(
    source: str, slippage_bp: int, backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload("strategy_eval")
    payload["execution"]["fill"] = {"type": "limit_price", "slippage_bp": slippage_bp}
    payload["strategy"]["quote"] = {"buy_offset_bp": 10, "sell_offset_bp": 0}
    if source == "recorded":
        payload["signal"] = {"type": "recorded", "prediction_refs": ["/data/fixed.parquet"]}

    submission = parse_job_request(payload)[0]
    command = build_cli_command(submission, "strategy-price")
    fields = json.loads(command[command.index("--input-json") + 1])

    assert submission.execution.fill.slippage_bp == slippage_bp
    assert submission.strategy.quote.buy_offset_bp == 10
    assert fields["execution"]["fill"]["slippage_bp"] == slippage_bp
    assert parse_job_request({"kind": "backtest", "mode": "strategy_eval", **fields}) == [submission]
    assert "risk" not in fields


@pytest.mark.parametrize("missing", ["slippage_bp", "quote"])
def test_strategy_evaluation_rejects_incomplete_price_model(
    missing: str, backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload("strategy_eval")
    payload["execution"]["fill"] = {"type": "limit_price"}
    if missing != "slippage_bp":
        payload["execution"]["fill"]["slippage_bp"] = 0
    if missing == "quote":
        payload["strategy"].pop("quote")

    message = "Field required" if missing == "slippage_bp" else "strategy.quote"
    with pytest.raises(InvalidJobRequest, match=message) as caught:
        parse_job_request(payload)
    assert caught.value.field == ("execution.fill.slippage_bp" if missing == "slippage_bp" else "request")


@pytest.mark.parametrize("price_model", [False, True])
@pytest.mark.parametrize(("risk_input", "error_field", "message"), [
    ("risk", "risk", "Extra inputs are not permitted"),
    ("risk_check_ts_us", "request", "does not run risk checks"),
])
def test_strategy_evaluation_rejects_business_risk_with_either_execution_model(
    price_model: bool, risk_input: str, error_field: str, message: str,
    backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload("strategy_eval")
    if price_model:
        payload["execution"]["fill"] = {"type": "limit_price", "slippage_bp": 0}
        payload["strategy"]["quote"] = {"buy_offset_bp": 0, "sell_offset_bp": 0}
    if risk_input == "risk":
        payload["risk"] = {"rules": [{"type": "max_single_name", "max_ratio": 0.2}]}
    else:
        payload["execution"]["risk_check_ts_us"] = [1]

    with pytest.raises(InvalidJobRequest, match=message) as caught:
        parse_job_request(payload)
    assert caught.value.field == error_field


@pytest.mark.parametrize("mode", ["signal_eval", "strategy_eval", "risk_rule_eval", "full_backtest"])
@pytest.mark.parametrize("source", ["model", "recorded"])
def test_backtest_requires_explicit_listing_age(
    mode: str, source: str, backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload(mode)
    payload.pop("min_listing_calendar_days", None)
    if source == "recorded":
        payload["signal"] = {"type": "recorded", "prediction_refs": []}
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == "min_listing_calendar_days"


@pytest.mark.parametrize("mode", ["signal_eval", "strategy_eval", "risk_rule_eval", "full_backtest"])
@pytest.mark.parametrize("source", ["model", "recorded"])
@pytest.mark.parametrize("listing_age", [0, 90])
def test_backtest_accepts_task_listing_age_and_preserves_it_in_cli_input(
    mode: str, source: str, listing_age: int,
    backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload(mode)
    payload["min_listing_calendar_days"] = listing_age
    if source == "recorded":
        payload["signal"] = {"type": "recorded", "prediction_refs": []}
    submission = parse_job_request(payload)[0]
    assert submission.min_listing_calendar_days == listing_age
    command = build_cli_command(submission, "listing-age")
    fields = json.loads(command[command.index("--input-json") + 1])
    assert fields["min_listing_calendar_days"] == listing_age
    assert "backtest_config" not in fields
    assert parse_job_request({"kind": "backtest", "mode": mode, **fields}) == [submission]


@pytest.mark.parametrize("mode", ["signal_eval", "strategy_eval", "risk_rule_eval", "full_backtest"])
@pytest.mark.parametrize("listing_age", [-1, True, 1.5, "120", None])
def test_backtest_rejects_invalid_listing_age(
    mode: str, listing_age: object, backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload(mode)
    payload["min_listing_calendar_days"] = listing_age
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == "min_listing_calendar_days"


@pytest.mark.parametrize("mode", ["execution_eval"])
def test_backtest_rejects_listing_age_without_signals(
    mode: str, backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload(mode)
    payload["min_listing_calendar_days"] = 120
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == "min_listing_calendar_days"


@pytest.mark.parametrize(("old_mode", "mode"), [
    ("tradable_alpha_eval", "strategy_eval"), ("risk_eval", "risk_rule_eval"),
])
def test_backtest_rejects_retired_mode_names(
    old_mode: str, mode: str, backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload(mode)
    payload["mode"] = old_mode
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == "mode"


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "data-standard"},
        {
            "kind": "data-standard",
            "date": "2026-07-20",
            "start": "2026-07-20",
            "end": "2026-07-20",
        },
        {
            "kind": "train",
            "start": "2026-07-20",
            "end": "2026-07-01",
        },
        {
            "kind": "backtest",
            "mode": "unknown",
            "start": "2026-07-01",
            "end": "2026-07-20",
            "model_ref": "/data/models/training-1.joblib",
            "strategy": {
                "type": "threshold",
                "params": {"threshold": 0.5},
            },
        },
    ],
)
def test_invalid_request_is_rejected_before_any_submission(
    payload: dict[str, object],
) -> None:
    with pytest.raises(InvalidJobRequest):
        parse_job_request(payload)


@pytest.mark.parametrize(("field", "value", "error_field"), [
    ("train_range", None, "train_range"),
    ("train_range", {}, "train_range.start"),
    ("train_range", {"start": "2026-7-1", "end": "2026-07-20"}, "train_range.start"),
    ("train_range", {"start": "2026-02-30", "end": "2026-07-20"}, "train_range.start"),
    ("train_range", {"start": "2026-07-21", "end": "2026-07-20"}, "train_range"),
    ("train_range", {"start": True, "end": "2026-07-20"}, "train_range.start"),
    ("train_range", {"start": "2026-07-01", "end": "2026-07-20", "train_dates": []}, "train_range.train_dates"),
    ("eval_range", {"start": "2026-08-01"}, "eval_range.end"),
    ("eval_range", {}, "eval_range.start"),
    ("model", "unknown", "model"),
    ("model", None, "model"),
    ("dataset", {"feature_group": "unknown", "label_group": "daily_close_return_rank_d1/v1"}, "dataset.feature_group"),
    ("dataset", {"feature_group": "daily_basic_selected_v1", "label_group": "daily_close_return_rank_d7/v1"}, "dataset.label_group"),
    ("dataset", {"feature_group": "daily_basic_selected_v1", "label_group": "l2_stock_1430_t1_vwap_rank/v1"}, "dataset"),
    ("dataset", {"feature_group": "l2_stock_1430_all_v1", "label_group": "daily_close_return_rank_d1/v1"}, "dataset"),
    ("dataset", {"feature_group": "stock_1430_daily_l2/v1", "label_group": "l2_stock_1430_t1_vwap_rank/v1"}, "dataset.feature_group"),
])
def test_training_request_rejects_invalid_ranges_and_choices(
    field: str, value: object, error_field: str,
) -> None:
    payload = _training_request()
    payload[field] = value
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == error_field


@pytest.mark.parametrize("field", [
    "windows", "train_dates", "training_cutoff_ts_us", "label_availability",
    "decision_ts_us", "availability_ref", "experiment_id", "model_params",
])
def test_training_rejects_removed_technical_inputs(field: str) -> None:
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request({**_training_request(), field: "not a business choice"})
    assert caught.value.field == field


@pytest.mark.parametrize("end", ["2026-08-10", "2026-08-31"])
def test_single_and_multiple_evaluation_days_share_one_request_shape(end: str) -> None:
    submission = parse_job_request({
        **_training_request(), "eval_range": {"start": "2026-08-10", "end": end},
    })[0]
    assert isinstance(submission, TrainingSubmission)
    assert submission.start == "2026-07-01" and submission.end == end
    command = build_cli_command(submission, "run-1")
    assert command[command.index("--eval-start") + 1] == "2026-08-10"
    assert command[command.index("--eval-end") + 1] == end


def test_omitted_and_null_evaluation_both_mean_pure_training() -> None:
    omitted = parse_job_request(_training_request())[0]
    explicit = parse_job_request({**_training_request(), "eval_range": None})[0]
    assert omitted == explicit


@pytest.mark.parametrize("ref", ["training-run", "relative/model.joblib", "../model.joblib", None, 1])
def test_backtest_requires_a_concrete_absolute_model_reference(
    ref: object, backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload("signal_eval")
    payload["signal"] = {"type": "model", "model_ref": ref}
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == "signal.model_ref"


def test_training_scope_covers_both_ranges_before_runtime_information_checks() -> None:
    submission = parse_job_request({
        **_training_request(), "train_range": {"start": "2026-07-20", "end": "2026-07-20"},
        "eval_range": {"start": "2026-07-02", "end": "2026-07-02"},
    })[0]
    assert (submission.start, submission.end) == ("2026-07-02", "2026-07-20")


def test_training_remains_one_job_with_explicit_gap() -> None:
    submissions = parse_job_request({**_training_request(), "eval_gap_sessions": 1, "validation_sessions": 27})
    assert len(submissions) == 1
    command = build_cli_command(submissions[0], "one-job")
    assert command[command.index("--eval-gap-sessions") + 1] == "1"
    assert command[command.index("--validation-sessions") + 1] == "27"


@pytest.mark.parametrize("field", ["recipe", "combinations", "retrain", "eval_ranges"])
def test_train_rejects_recipe_and_multi_job_expansion(field: str) -> None:
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request({**_training_request(), field: []})
    assert caught.value.field == field


@pytest.mark.parametrize("gap", [-1, True, 1.5, "1"])
@pytest.mark.parametrize("field", ["eval_gap_sessions", "validation_sessions"])
def test_session_counts_must_be_nonnegative_integers(gap: object, field: str) -> None:
    with pytest.raises(InvalidJobRequest):
        parse_job_request({**_training_request(), field: gap})


def test_signal_evaluation_accepts_fixed_predictions_with_an_account(
    backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload("signal_eval")
    payload["signal"] = {"type": "recorded", "prediction_refs": ["/data/fixed.parquet"]}
    submission = parse_job_request(payload)[0]
    assert submission.signal.prediction_refs == (Path("/data/fixed.parquet"),)
    assert submission.strategy.quote is not None
    assert submission.execution.fill.type == "quoted"


@pytest.mark.parametrize("field", ["strategy", "initial_account", "execution"])
def test_signal_evaluation_requires_trading_inputs(
    backtest_payload: Callable[[str], dict[str, object]], field: str,
) -> None:
    payload = backtest_payload("signal_eval")
    del payload[field]
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == field


@pytest.mark.parametrize(("section", "field", "value"), [
    ("strategy", "quote", None), ("strategy", "order_window_minutes", None),
    ("execution", "slippage_bp", 0), ("execution", "risk_check_ts_us", [1]),
    ("execution", "end_ts_us", 1),
])
def test_signal_evaluation_rejects_conflicting_fill_assumptions(
    backtest_payload: Callable[[str], dict[str, object]], section: str, field: str, value: object,
) -> None:
    payload = backtest_payload("signal_eval")
    payload[section][field] = value
    with pytest.raises(InvalidJobRequest):
        parse_job_request(payload)


@pytest.mark.parametrize(('field', 'value'), [('risk', None), ('risk', {'rules': []}),
    ('unexpected', True)])
def test_full_rejects_missing_or_empty_risk_and_unrelated_fields(
    backtest_payload: Callable[[BacktestMode | str], dict[str, object]], field: str, value: object,
) -> None:
    payload = backtest_payload('full_backtest')
    payload[field] = value
    with pytest.raises(InvalidJobRequest):
        parse_job_request(payload)


@pytest.mark.parametrize(('section', 'field', 'value'), [
    ('execution', 'fill', {'type': 'limit_price', 'slippage_bp': None}),
    ('execution', 'fill', {'type': 'limit_price', 'slippage_bp': float('nan')}),
    ('strategy', 'quote', None), ('strategy', 'order_window_minutes', True)])
def test_full_rejects_incomplete_execution_assumptions(
    backtest_payload: Callable[[BacktestMode | str], dict[str, object]],
    section: str, field: str, value: object,
) -> None:
    payload = backtest_payload('full_backtest')
    payload[section][field] = value
    with pytest.raises(InvalidJobRequest):
        parse_job_request(payload)


def test_quantity_rules_reject_coercion_and_unknown_fields(
    backtest_payload: Callable[[BacktestMode | str], dict[str, object]],
) -> None:
    payload = backtest_payload('full_backtest')
    rule = payload['execution']['quantity_rules']['600000']
    rule['buy_min_qty'] = True
    with pytest.raises(InvalidJobRequest):
        parse_job_request(payload)
    rule['buy_min_qty'] = 100
    rule['lot_guess'] = 100
    with pytest.raises(InvalidJobRequest):
        parse_job_request(payload)


@pytest.mark.parametrize('mode', list(BacktestMode))
def test_backtest_requires_an_explicit_fill_model(
    mode: BacktestMode, backtest_payload: Callable[[BacktestMode], dict[str, object]],
) -> None:
    payload = backtest_payload(mode)
    del payload['execution']['fill']
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == 'execution.fill'


@pytest.mark.parametrize(('mode', 'fill'), [
    ('strategy_eval', {'type': 'unknown'}),
    ('signal_eval', {'type': 'limit_price', 'slippage_bp': 0}),
    ('full_backtest', {'type': 'quoted'}),
    ('execution_eval', {'type': 'quoted'}),
    ('strategy_eval', {'type': 'limit_price'}),
    ('risk_rule_eval', {'type': 'quoted', 'slippage_bp': 0}),
])
def test_backtest_rejects_fill_models_outside_the_declared_mode_contract(
    mode: str, fill: dict[str, object], backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload(mode)
    payload['execution']['fill'] = fill
    with pytest.raises(InvalidJobRequest):
        parse_job_request(payload)


def test_risk_effectiveness_rejects_archived_case_inputs(
    backtest_payload: Callable[[str], dict[str, object]],
) -> None:
    payload = backtest_payload('risk_rule_eval')
    payload['case_refs'] = ['/data/cases.json']
    with pytest.raises(InvalidJobRequest) as caught:
        parse_job_request(payload)
    assert caught.value.field == 'case_refs'
