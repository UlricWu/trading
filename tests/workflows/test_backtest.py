# filepath: tests/workflows/test_backtest.py
"""Fixed-model delivery through actual backtest composition and persisted replay."""

import json
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pytest_mock import MockerFixture

from src import logs
from src.access import Access, meta
from src.config.backtest_config import BacktestMode
from src.config.model_config import FeatureGroupConfig, MissingConfig, ModelPreset, PreprocessingConfig
from src.jobs.requests import create_backtest_submission, create_training_submission
from src.observability.instrumentation import Instrumentation
from src.training.evaluation import evaluate_prediction_record
from src.training.prediction import read_prediction_record, verify_prediction_record
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.filesystem import FileSystem
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager
from src.workflows.backtest import run_backtest
from src.training.artifact import load
from src.training.prediction import save_prediction_record
from src.trading.backtest.market.replay import ReplayMarketInput
from src.trading.backtest.market import signals
from src.trading.backtest.context import BacktestResultContext
from src.trading.backtest.reporting.metrics import BacktestMetrics
from src.trading.backtest.steps.evaluation import SignalEvaluationStep
from src.trading.backtest.market.data_view import ExecutionObservation
from src.trading.core.time import US_PER_MINUTE
from src.workflows.offline_training import run_offline_training


def _decision(day: str) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(time(15, 0), date.fromisoformat(day))


@pytest.fixture
def backtest_log(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "job.log"
    sink = logs.add(
        path, format="{message}", level="INFO",
        filter=lambda record: record["name"] == "src.workflows.backtest"
        or record["name"] == "src.observability.instrumentation"
        or record["name"].startswith("src.trading."),
    )
    try:
        yield path
    finally:
        logs.remove(sink)


@pytest.fixture
def delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[PathManager, Path, Mock]:
    pm = PathManager(tmp_path)
    days = ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07")
    access = Mock(spec=Access)
    access.trade_dates.side_effect = lambda *, start_date, end_date: [
        day for day in days if start_date <= day <= end_date
    ]
    access.next_trade_date.side_effect = lambda *, trade_date: days[days.index(trade_date) + 1]
    access.universe.return_value = ("600000", "000002", "000001")
    access.daily_bars.side_effect = lambda **kwargs: pd.DataFrame({
        "symbol": ["000001", "000002", "600000"], "close": [10.0, 20.0, 30.0],
    })
    monkeypatch.setattr("src.workflows.offline_training.Access", Mock(return_value=access))
    monkeypatch.setattr("src.workflows.backtest.Access", Mock(return_value=access))
    for day in ("2026-07-01", "2026-07-03", "2026-07-06"):
        keys = {"symbol": ["000001", "000002", "600000"], "trade_date": [day] * 3}
        feature_ref = pm.feature_object(
            feature_set="tushare_daily_basic", version="v1", trade_date=day,
        ).payload_path
        label_ref = pm.label_object(
            label_set="daily_close_return_rank_d1", version="v1", trade_date=day,
        ).payload_path
        for ref, column, values in (
            (feature_ref, "f_d_close_return_1d", [0.0, np.nan, 2.0]),
            (label_ref, "y_rank_return", [0.25, np.nan, 0.75]),
        ):
            ref.parent.mkdir(parents=True)
            pd.DataFrame({**keys, column: values}).to_parquet(ref, index=False)
            meta.commit(pm=pm, payload_path=ref)
    preset = ModelPreset(
        group="sgd_regression", model_params={"random_state": 7},
        preprocessing=PreprocessingConfig(missing=MissingConfig(method="drop")),
    )
    feature = FeatureGroupConfig(
        feature_set="tushare_daily_basic", feature_version="v1",
        feature_columns=["f_d_close_return_1d"],
    )
    run_offline_training(
        models={"sgd_regression": preset}, feature_groups={"daily_basic_selected_v1": feature},
        path_manager=pm, experiment_id="delivery",
        submission=create_training_submission(
            train_range={"start": "2026-07-01", "end": "2026-07-01"},
            model="sgd_regression", dataset={
                "feature_group": "daily_basic_selected_v1",
                "label_group": "daily_close_return_rank_d1/v1",
            },
        ),
    )
    training_name = "training_2026-07-01_2026-07-01_delivery"
    model_ref = pm.experiment_training_model(experiment_name=training_name)
    return pm, model_ref, access


def _ts(day: str, hour: int, minute: int = 0) -> int:
    return DateTimeUtils.local_time_to_utc_epoch_us(time(hour, minute), date.fromisoformat(day))


@pytest.fixture
def scenario(delivery: tuple[PathManager, Path, Mock], tmp_path: Path) -> tuple[PathManager, dict[str, object], Mock]:
    pm, model_ref, access = delivery
    day = "2026-07-03"
    ts = _ts(day, 14, 30)
    model = load(model_ref)
    features = pd.read_parquet(pm.feature_object(feature_set=model.feature_set, version=model.feature_version,
                                                 trade_date=day).payload_path)
    predictions = model.predict(features[list(model.feature_names)].to_numpy())
    prediction_ref = tmp_path / "fixed.parquet"
    save_prediction_record(prediction_ref, model_ref=model_ref, trade_date=day, decision_ts_us=ts,
        features=features, feature_names=model.feature_names, predictions=predictions)
    price = {"600000": "10", "000001": "10", "000002": "10"}
    market = {
        "price_basis": "raw", "quantity_unit": "shares",
        "sessions": [{"trade_date": day, "start_ts_us": _ts(day, 0), "close_ts_us": _ts(day, 15),
                      "windows": [{"trade_date": day, "start_ts_us": _ts(day, 9, 30), "end_ts_us": _ts(day, 11, 30)},
                                  {"trade_date": day, "start_ts_us": _ts(day, 13), "end_ts_us": _ts(day, 15)}]}],
        "marks": [{"ts_us": ts, "trade_date": day, "prices": price, "kind": "mark"},
                  {"ts_us": _ts(day, 15), "trade_date": day, "prices": price, "kind": "daily_close"}],
        "observations": [{"observation_id": f"minute-{index}", "symbol": "600000", "time": {
            "unit": "minute", "size": 1, "trade_date": day,
            "start_ts_us": ts + index * US_PER_MINUTE, "end_ts_us": ts + (index + 1) * US_PER_MINUTE,
            "available_ts_us": ts + (index + 1) * US_PER_MINUTE,
        }, "price": "10", "volume_qty": volume} for index, volume in enumerate((300, 60))],
    }
    market_ref = tmp_path / "market.json"
    market_ref.write_text(json.dumps(market))
    rule = {"buy_min_qty": 1, "buy_step_qty": 1, "sell_min_qty": 1, "sell_step_qty": 1,
            "fill_step_qty": 1, "price_tick": "0.01", "enforce_t1": True}
    fields = {
        "start": day, "end": day, "min_listing_calendar_days": 120,
        "signal": {"type": "recorded", "prediction_refs": [str(prediction_ref)]},
        "strategy": {"type": "threshold", "params": {"threshold": float((predictions[0] + predictions[2]) / 2),
                     "target_quantity": 1000}, "order_window_minutes": 2,
                     "quote": {"buy_offset_bp": "0", "sell_offset_bp": "0"}},
        "risk": {"rules": [{"type": "max_single_name", "max_ratio": 0.2}]},
        "initial_account": {"ts_us": ts, "trade_date": day, "cash": "20000", "positions": {},
                            "sellable": {}, "average_cost": {}, "peak_equity": 20000},
        "execution": {"market_ref": str(market_ref), "end_ts_us": _ts(day, 15),
                      "quantity_rules": {symbol: rule for symbol in price},
                      "fees": {"commission_rate": 0, "stamp_tax_rate": 0, "transfer_rate": 0, "min_commission": 0},
                      "annual_factor": 252, "fill": {"type": "limit_price", "slippage_bp": 0}, "risk_check_ts_us": [ts + 3 * US_PER_MINUTE]},
    }
    return pm, fields, access


@pytest.fixture
def signal_scenario(scenario: tuple[PathManager, dict[str, object], Mock]) -> tuple[PathManager, dict[str, object], Mock]:
    pm, fields, access = scenario
    fields.pop("risk")
    fields["execution"]["fill"] = {"type": "quoted"}
    fields["execution"].pop("risk_check_ts_us")
    fields["execution"]["end_ts_us"] = _ts("2026-07-06", 15)
    ref = Path(fields["execution"]["market_ref"])
    market = json.loads(ref.read_text())
    market["sessions"].append({"trade_date": "2026-07-06", "start_ts_us": _ts("2026-07-06", 0),
        "close_ts_us": _ts("2026-07-06", 15), "windows": [
            {"trade_date": "2026-07-06", "start_ts_us": _ts("2026-07-06", 9, 30), "end_ts_us": _ts("2026-07-06", 11, 30)},
            {"trade_date": "2026-07-06", "start_ts_us": _ts("2026-07-06", 13), "end_ts_us": _ts("2026-07-06", 15)},
        ]})
    market["marks"].append({"ts_us": _ts("2026-07-06", 15), "trade_date": "2026-07-06", "kind": "daily_close",
                            "prices": {"600000": "10", "000001": "10", "000002": "10"}})
    ref.write_text(json.dumps(market))
    return pm, fields, access


def _metrics(pm: PathManager, name: str) -> dict[str, object]:
    return json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())


@pytest.mark.parametrize("source", ["recorded", "model"])
def test_signal_evaluation_reports_quote_fills_and_net_account_profit(
    signal_scenario: tuple[PathManager, dict[str, object], Mock], source: str,
) -> None:
    pm, fields, _ = signal_scenario
    if source == "model":
        inputs, _ = read_prediction_record(Path(fields["signal"]["prediction_refs"][0]))
        fields["signal"] = {"type": "model", "model_ref": str(inputs.model_ref)}
        fields["initial_account"]["ts_us"] = _ts(fields["start"], 15)
    fields["strategy"]["quote"]["buy_offset_bp"] = "100"
    fields["execution"]["fees"]["min_commission"] = "5"
    ref = Path(fields["execution"]["market_ref"])
    market = json.loads(ref.read_text())
    market["observations"] = []
    market["marks"][-1]["prices"]["600000"] = "11"
    ref.write_text(json.dumps(market))
    submission = create_backtest_submission(mode="signal_eval", **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    name = f"backtest_2026-10-11_signal-profit-{source}"

    run_backtest(submission=submission, path_manager=pm, experiment_name=name, metrics=metrics)

    result = _metrics(pm, name)
    assert result["signal_eval"]["mean_ic"] == 1
    assert result["final_account"]["positions"] == {"600000": 1000}
    assert result["final_account"]["sellable"] == {"600000": 1000}
    assert Decimal(result["final_account"]["cash"]) == 9895
    portfolio = result["portfolio"]
    assert portfolio["initial_equity"] == 20000
    assert portfolio["final_equity"] == 20895
    assert portfolio["net_profit"] == 895
    assert portfolio["total_return"] == pytest.approx(895 / 20000)
    assert Decimal(portfolio["total_fees"]) == 5
    assert portfolio["max_drawdown"] == pytest.approx(105 / 20000)
    assert result["execution_eval"]["order_completion_rate"] == 1
    fills = [row for row in pq.read_table(pm.experiment_backtest_execution(experiment_name=name)).to_pylist()
             if row["event"] == "FILL"]
    assert [(row["ts_us"], row["qty"], Decimal(row["price"]), row["observation_id"]) for row in fills] == [
        (fields["initial_account"]["ts_us"], 1000, Decimal("10.10"), None),
    ]
    equity = pq.read_table(pm.experiment_backtest_equity(experiment_name=name)).to_pylist()
    assert [row["equity"] for row in equity] == [19895] * (3 if source == "recorded" else 2) + [20895]
    report = pm.experiment_backtest_report(experiment_name=name).read_text()
    assert "生效时按申报价全额成交" in report and "net_profit" in report
    assert 'href="equity.parquet"' in report
    assert "risk_eval" not in result


def test_signal_fixed_holding_settles_scheduled_exit_without_another_prediction(
    signal_scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, _ = signal_scenario
    fields["strategy"] = {"type": "fixed_holding", "params": {"max_positions": 1,
        "decision_time": "14:30:00", "exit_time": "14:00:00", "initial_exit_dates": {}},
        "order_window_minutes": 2, "quote": {"buy_offset_bp": 0, "sell_offset_bp": 0}}
    fields["execution"]["fees"]["min_commission"] = 5
    market_ref = Path(fields["execution"]["market_ref"])
    market = json.loads(market_ref.read_text())
    market["observations"] = []
    market["marks"].insert(-1, {"ts_us": _ts("2026-07-06", 14), "trade_date": "2026-07-06",
                               "kind": "mark", "prices": {"600000": "11"}})
    market_ref.write_text(json.dumps(market))
    submission = create_backtest_submission(mode="signal_eval", **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    name = "backtest_2026-10-11_signal-exit"

    run_backtest(submission=submission, path_manager=pm, experiment_name=name, metrics=metrics)

    result = _metrics(pm, name)
    assert result["signal_eval"]["decision_count"] == 1
    assert result["final_account"]["positions"] == {}
    assert Decimal(result["final_account"]["cash"]) == 21989
    assert result["portfolio"]["net_profit"] == 1989
    assert Decimal(result["portfolio"]["total_fees"]) == 10
    fills = [row for row in pq.read_table(pm.experiment_backtest_execution(experiment_name=name)).to_pylist()
             if row["event"] == "FILL"]
    assert [(row["ts_us"], row["side"], row["qty"]) for row in fills] == [
        (_ts("2026-07-03", 14, 30), "BUY", 1999), (_ts("2026-07-06", 14), "SELL", 1999),
    ]


def test_signal_without_predictions_values_existing_holdings_without_trades(
    signal_scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, _ = signal_scenario
    fields["signal"]["prediction_refs"] = []
    fields["initial_account"].update(cash="0", positions={"600000": 100},
        sellable={"600000": 100}, average_cost={"600000": "8"}, peak_equity=1000)
    ref = Path(fields["execution"]["market_ref"])
    market = json.loads(ref.read_text())
    market["marks"][-1]["prices"]["600000"] = "11"
    ref.write_text(json.dumps(market))
    submission = create_backtest_submission(mode="signal_eval", **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    name = "backtest_2026-10-11_no-signal"

    run_backtest(submission=submission, path_manager=pm, experiment_name=name, metrics=metrics)

    result = _metrics(pm, name)
    assert result["signal_eval"]["decision_count"] == 0
    assert result["signal_eval"]["mean_ic"] is None
    assert result["execution_eval"]["order_count"] == 0
    assert result["portfolio"]["initial_equity"] == 1000
    assert result["portfolio"]["net_profit"] == 100
    assert result["portfolio"]["total_return"] == pytest.approx(0.1)


@pytest.mark.parametrize("mode", ["signal_eval", "strategy_eval", "risk_rule_eval", "full_backtest"])
def test_trading_preparation_reads_each_prediction_and_model_once(
    scenario: tuple[PathManager, dict[str, object], Mock], mocker: MockerFixture, mode: str,
) -> None:
    pm, fields, _ = scenario
    if mode in ("signal_eval", "strategy_eval"):
        del fields["risk"]
        fields["execution"]["risk_check_ts_us"] = []
    if mode == "signal_eval":
        fields["execution"]["fill"] = {"type": "quoted"}
    submission = create_backtest_submission(mode=mode, **fields)
    read_record = mocker.spy(signals, "read_prediction_record")
    load_model = mocker.spy(signals, "load_prediction_model")
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})

    run_backtest(submission=submission, path_manager=pm,
                 experiment_name=f"backtest_2026-10-11_read-once-{mode}", metrics=metrics)

    read_record.assert_called_once_with(Path(fields["signal"]["prediction_refs"][0]))
    load_model.assert_called_once()


def test_full_pipeline_closes_target_risk_partial_fills_and_expiry(
    scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, _ = scenario
    submission = create_backtest_submission(mode="full_backtest", **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_full', metrics=metrics)
    name = "backtest_2026-10-11_full"
    metrics = _metrics(pm, name)
    assert Decimal(metrics["final_account"]["cash"]) == Decimal("16400")
    assert metrics["final_account"]["positions"] == {"600000": 360}
    assert metrics["execution_eval"]["requested_qty"] == 400
    assert metrics["execution_eval"]["filled_qty"] == 360
    assert metrics["execution_eval"]["orders"][0]["remaining_qty"] == 40
    assert metrics["execution_eval"]["orders"][0]["status"] == "expired"
    cases = metrics["risk_eval"]["observations"]
    assert cases[0]["case"]["target"]["600000"] == 1000
    assert cases[0]["decision"]["adjusted"]["600000"] == 400
    assert all(item["decision"]["adjusted"] is None for item in cases[1:])
    assert metrics["portfolio"]["total_return"] == 0
    assert metrics["portfolio"]["sharpe"] is None
    assert "not_run" in metrics
    assert pm.experiment_backtest_report(experiment_name=name).is_file()


def test_full_facts_reproduce_independent_signal_risk_and_execution(
    scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, access = scenario
    access.universe.reset_mock()
    access.universe.side_effect = AssertionError('recorded signals must not be filtered again')
    submission = create_backtest_submission(mode="full_backtest", **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_original', metrics=metrics)
    original_name = "backtest_2026-10-11_original"
    original = _metrics(pm, original_name)
    run_dir = pm.experiment_dir(experiment_name=original_name)
    submission = create_backtest_submission(mode="signal_eval",
        start=fields["start"], end=fields["end"], signal=fields["signal"],
        min_listing_calendar_days=0, strategy=fields["strategy"], initial_account=fields["initial_account"],
        execution={**fields["execution"], "fill": {"type": "quoted"}, "risk_check_ts_us": []})
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_signal', metrics=metrics)
    signal = _metrics(pm, "backtest_2026-10-11_signal")
    assert original["parameters"]["min_listing_calendar_days"] == 120
    assert signal["parameters"]["min_listing_calendar_days"] == 0
    access.universe.assert_not_called()
    assert signal["signal_eval"] == original["signal_eval"]
    assert signal["final_account"]["positions"] == {"600000": 1000}
    assert signal["execution_eval"]["filled_qty"] == 1000
    assert pm.experiment_backtest_execution(experiment_name="backtest_2026-10-11_signal").is_file()
    from src.trading.risk import RiskCases, RiskManager
    from src.config.backtest_config import RiskConfig
    from src.trading.backtest.evaluation import evaluate_risk
    risk_config = RiskConfig.model_validate(fields["risk"])
    observations = RiskCases.from_files((run_dir / "risk-cases.json",)).evaluate(
        risk=RiskManager.from_config(risk_config))
    risk = evaluate_risk(observations, configured_rules=tuple(rule.type for rule in risk_config.rules))
    assert risk.model_dump(mode="json") == original["risk_eval"]
    execution = dict(fields["execution"])
    execution["risk_check_ts_us"] = []
    submission = create_backtest_submission(mode="execution_eval",
        start=fields["start"], end=fields["end"], order_refs=[str(run_dir / "orders.json")],
        initial_account=fields["initial_account"], execution=execution)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_execution', metrics=metrics)
    replay = _metrics(pm, "backtest_2026-10-11_execution")
    assert replay["execution_eval"] == original["execution_eval"]
    assert replay["final_account"] == original["final_account"]
    assert "signal_eval" not in replay and "risk_eval" not in replay


def test_reference_fills_are_account_constrained_and_ignore_quote_and_market_volume(
    scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, _ = scenario
    fields.pop("risk")
    fields["strategy"].pop("quote")
    fields["execution"]["fill"] = {"type": "reference"}
    fields["execution"].pop("risk_check_ts_us")
    fields["initial_account"]["cash"] = "5000"
    fields["initial_account"]["peak_equity"] = 5000
    submission = create_backtest_submission(mode="strategy_eval", **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_reference', metrics=metrics)
    metrics = _metrics(pm, "backtest_2026-10-11_reference")
    assert metrics["mode"] == metrics["parameters"]["mode"] == "strategy_eval"
    assert metrics["final_account"]["positions"] == {"600000": 500}
    assert metrics["final_account"]["cash"] == "0"
    assert metrics["execution_eval"]["filled_qty"] == 500 and "risk_eval" not in metrics
    assert "signal_eval" in metrics
    assert metrics["not_run"]["price_feasibility"] == "reference_fills"


@pytest.mark.parametrize(('slippage_bp', 'buy_offset_bp', 'filled_qty', 'fill_price'), [
    (0, 0, 360, Decimal('10')),
    (10, 0, 0, None),
    (10, 10, 360, Decimal('10.01')),
])
def test_strategy_price_model_limits_fills_and_replays_without_business_risk(
    scenario: tuple[PathManager, dict[str, object], Mock], slippage_bp: int,
    buy_offset_bp: int, filled_qty: int, fill_price: Decimal | None,
) -> None:
    pm, fields, _ = scenario
    fields.pop('risk')
    fields['execution'].pop('risk_check_ts_us')
    fields['execution']['fill']['slippage_bp'] = slippage_bp
    fields['execution']['fees'].update(commission_rate='0.001', min_commission='5')
    fields['strategy']['quote']['buy_offset_bp'] = buy_offset_bp
    submission = create_backtest_submission(mode='strategy_eval', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode='json'), not_run={})
    name = 'backtest_2026-10-11_strategy-price'

    run_backtest(submission=submission, path_manager=pm, experiment_name=name, metrics=metrics)

    result = _metrics(pm, name)
    run_dir = pm.experiment_dir(experiment_name=name)
    orders = json.loads((run_dir / 'orders.json').read_text())
    facts = pq.read_table(pm.experiment_backtest_execution(experiment_name=name)).to_pylist()
    fills = [row for row in facts if row['event'] == 'FILL']
    assert len(orders) == 1 and orders[0]['qty'] == 1000
    assert Decimal(orders[0]['limit_price']) == Decimal('10') * (1 + Decimal(buy_offset_bp) / 10000)
    assert [row['qty'] for row in fills] == ([300, 60] if filled_qty else [])
    assert all(Decimal(row['price']) == fill_price for row in fills)
    assert [row['ts_us'] for row in fills] == ([_ts('2026-07-03', 14, 31), _ts('2026-07-03', 14, 32)] if filled_qty else [])
    expired = [row for row in facts if row['status'] == 'expired']
    assert [(row['ts_us'], row['remaining_qty']) for row in expired] == [(_ts('2026-07-03', 14, 32), 1000 - filled_qty)]
    assert result['final_account']['positions'] == ({'600000': filled_qty} if filled_qty else {})
    expected_cash = Decimal('20000') - (fill_price * filled_qty + 5 if fill_price is not None else 0)
    assert Decimal(result['final_account']['cash']) == expected_cash
    assert Decimal(result['portfolio']['total_fees']) == (5 if filled_qty else 0)
    assert result['not_run'] == {'risk': 'no_business_risk'}
    assert result['execution_eval']['filled_qty'] == filled_qty
    assert 'signal_eval' in result
    assert 'risk_eval' not in result and not (run_dir / 'risk-cases.json').exists()
    assert result['prediction_refs'] == fields['signal']['prediction_refs']
    assert result['parameters'] == submission.model_dump(mode='json')
    assert 'reference_fills' not in pm.experiment_backtest_report(experiment_name=name).read_text()

    replay_submission = create_backtest_submission(mode='execution_eval', start=fields['start'], end=fields['end'],
        order_refs=[str(run_dir / 'orders.json')], initial_account=fields['initial_account'], execution=fields['execution'])
    replay_metrics = BacktestMetrics(mode=replay_submission.mode, parameters=replay_submission.model_dump(mode='json'), not_run={})
    replay_name = 'backtest_2026-10-11_strategy-orders'
    run_backtest(submission=replay_submission, path_manager=pm, experiment_name=replay_name, metrics=replay_metrics)
    assert _metrics(pm, replay_name)['final_account'] == result['final_account']
    assert pq.read_table(pm.experiment_backtest_execution(experiment_name=replay_name)).to_pylist() == facts


def test_strategy_price_model_requires_observed_volume_before_publishing(
    scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, _ = scenario
    fields.pop('risk')
    fields['execution'].pop('risk_check_ts_us')
    market_ref = Path(fields['execution']['market_ref'])
    market = json.loads(market_ref.read_text())
    for observation in market['observations']:
        observation['volume_qty'] = None
    market_ref.write_text(json.dumps(market))
    submission = create_backtest_submission(mode='strategy_eval', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode='json'), not_run={})
    name = 'backtest_2026-10-11_missing-volume'

    with pytest.raises(ValueError, match='explicit observed volume_qty'):
        run_backtest(submission=submission, path_manager=pm, experiment_name=name, metrics=metrics)
    assert not pm.experiment_dir(experiment_name=name).exists()


def test_fixed_holding_exits_without_signal_retries_and_retains_unfilled_end_position(
    scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, _ = scenario
    ref = Path(fields['execution']['market_ref'])
    market = json.loads(ref.read_text())
    for day in ('2026-07-06', '2026-07-07'):
        market['sessions'].append({'trade_date': day, 'start_ts_us': _ts(day, 0), 'close_ts_us': _ts(day, 15),
            'windows': [{'trade_date': day, 'start_ts_us': _ts(day, 9, 30), 'end_ts_us': _ts(day, 11, 30)},
                        {'trade_date': day, 'start_ts_us': _ts(day, 13), 'end_ts_us': _ts(day, 15)}]})
        market['marks'].append({'ts_us': _ts(day, 15), 'trade_date': day, 'kind': 'daily_close',
                                'prices': {'600000': '10', '000001': '10', '000002': '10'}})
    ts = _ts('2026-07-07', 14)
    market['observations'].append({'observation_id': 'partial-exit', 'symbol': '600000',
        'time': {'unit': 'minute', 'size': 1, 'trade_date': '2026-07-07', 'start_ts_us': ts,
                 'end_ts_us': ts + US_PER_MINUTE, 'available_ts_us': ts + US_PER_MINUTE},
        'price': '10', 'volume_qty': 100})
    ref.write_text(json.dumps(market))
    fields['execution']['end_ts_us'] = _ts('2026-07-07', 15)
    fields['strategy'] = {'type': 'fixed_holding', 'params': {'max_positions': 1,
        'decision_time': '14:30:00', 'exit_time': '14:00:00', 'initial_exit_dates': {}},
        'order_window_minutes': 2, 'quote': {'buy_offset_bp': 0, 'sell_offset_bp': 0}}
    submission = create_backtest_submission(mode='full_backtest', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_scheduled', metrics=metrics)
    run_dir = pm.experiment_dir(experiment_name='backtest_2026-10-11_scheduled')
    result = _metrics(pm, run_dir.name)
    assert result['signal_eval']['decision_count'] == 1
    assert result['final_account']['positions'] == {'600000': 260}
    assert Decimal(result['final_account']['cash']) == 17400
    orders = [cmd for cmd in json.loads((run_dir / 'orders.json').read_text()) if 'side' in cmd]
    assert [(o['side'], o['ts_us'], o['qty']) for o in orders] == [
        ('BUY', _ts('2026-07-03', 14, 30), 400), ('SELL', _ts('2026-07-06', 14), 360),
        ('SELL', _ts('2026-07-07', 14), 360)]
    assert result['portfolio']['daily_returns'] == {'2026-07-03': 0, '2026-07-06': 0, '2026-07-07': 0}
    assert all(not row.get('forced_close', False) for row in pq.read_table(run_dir / 'execution.parquet').to_pylist())


@pytest.fixture
def daily_scenario(scenario: tuple[PathManager, dict[str, object], Mock]) -> tuple[PathManager, dict[str, object], Mock]:
    pm, fields, access = scenario
    inputs, _ = read_prediction_record(Path(fields['signal']['prediction_refs'][0]))
    fields['signal'] = {'type': 'model', 'model_ref': str(inputs.model_ref)}
    fields['end'] = '2026-07-06'
    fields['risk'] = {'rules': [{'type': 'max_single_name', 'max_ratio': 1.0}]}
    fields['strategy']['params'] = {'threshold': -1.123456789, 'target_quantity': 100}
    fields['execution']['end_ts_us'] = _ts('2026-07-07', 15)
    fields['execution']['risk_check_ts_us'] = []
    ref = Path(fields['execution']['market_ref'])
    market = json.loads(ref.read_text())
    for day in ('2026-07-06', '2026-07-07'):
        market['sessions'].append({'trade_date': day, 'start_ts_us': _ts(day, 0), 'close_ts_us': _ts(day, 15),
            'windows': [{'trade_date': day, 'start_ts_us': _ts(day, 9, 30), 'end_ts_us': _ts(day, 11, 30)},
                        {'trade_date': day, 'start_ts_us': _ts(day, 13), 'end_ts_us': _ts(day, 15)}]})
        market['marks'].append({'ts_us': _ts(day, 15), 'trade_date': day, 'kind': 'daily_close',
                                'prices': {'600000': '10', '000001': '10', '000002': '10'}})
        for symbol in ('600000', '000001'):
            ts = _ts(day, 9, 30)
            market['observations'].append({'observation_id': f'{day}-{symbol}', 'symbol': symbol,
                'time': {'unit': 'minute', 'size': 1, 'trade_date': day, 'start_ts_us': ts,
                         'end_ts_us': ts + US_PER_MINUTE, 'available_ts_us': ts + US_PER_MINUTE},
                'price': '10', 'volume_qty': 300})
    ref.write_text(json.dumps(market))
    return pm, fields, access


def test_daily_model_inference_preserves_rows_and_all_skipped_holdings(daily_scenario: tuple[PathManager, dict[str, object], Mock]) -> None:
    pm, fields, access = daily_scenario
    fields['min_listing_calendar_days'] = 90
    access.universe.reset_mock()
    feature_ref = pm.feature_object(feature_set='tushare_daily_basic', version='v1', trade_date='2026-07-06').payload_path
    features = pd.read_parquet(feature_ref)
    features['f_d_close_return_1d'] = np.nan
    features.to_parquet(feature_ref, index=False)
    meta.commit(pm=pm, payload_path=feature_ref)
    original_model = Path(fields['signal']['model_ref']).read_bytes()
    submission = create_backtest_submission(mode='full_backtest', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_daily', metrics=metrics)
    name = 'backtest_2026-10-11_daily'
    result = _metrics(pm, name)
    assert result['final_account']['positions'] == {'000001': 100, '600000': 100}
    assert result['execution_eval']['order_count'] == 2
    assert result['signal_eval']['decision_count'] == result['signal_eval']['trade_day_count'] == 2
    for ref in result['prediction_refs']:
        verify_prediction_record(Path(ref), pm=pm)
    inputs, rows = read_prediction_record(Path(result['prediction_refs'][1]))
    assert inputs.decision_ts_us == _ts('2026-07-06', 15)
    assert rows['symbol'].tolist() == ['000001', '000002', '600000']
    assert rows['prediction'].isna().all()
    observation = result['signal_eval']['observations'][1]
    assert observation['input_count'] == observation['no_prediction_count'] == 3
    assert observation['value'] is None and observation['undefined_reasons'] == ['insufficient_pairs']
    assert original_model == Path(fields['signal']['model_ref']).read_bytes()
    assert result['parameters']['min_listing_calendar_days'] == 90
    assert result['parameters'] == metrics.parameters
    assert [call.kwargs for call in access.universe.call_args_list] == [
        {'trade_date': day, 'min_listing_calendar_days': 90} for day in ('2026-07-03', '2026-07-06')]


@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_model_maturity_boundary_precedes_first_selected_decision(daily_scenario: tuple[PathManager, dict[str, object], Mock], offset: int) -> None:
    pm, fields, _ = daily_scenario
    model_ref = Path(fields['signal']['model_ref'])
    ref = model_ref.with_name('schedule.json')
    record = json.loads(ref.read_text())
    record['label_maturity_ts_us'] = _ts('2026-07-06', 15) + offset
    record['request'] = {'archived_intent': 'not a current request'}
    ref.write_text(json.dumps(record))
    submission = create_backtest_submission(mode='signal_eval', start='2026-07-04', end='2026-07-06',
        signal={'type': 'model', 'model_ref': str(model_ref)}, min_listing_calendar_days=120,
        strategy=fields['strategy'], initial_account=fields['initial_account'],
        execution={**fields['execution'], 'fill': {'type': 'quoted'}, 'risk_check_ts_us': []})
    name = 'backtest_2026-10-11_maturity'
    if offset > 0:
        with pytest.raises(ValueError, match='label maturity'):
            metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
            run_backtest(submission=submission, path_manager=pm,
                experiment_name='backtest_2026-10-11_maturity', metrics=metrics)
        assert not pm.experiment_dir(experiment_name=name).exists()
    else:
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name='backtest_2026-10-11_maturity', metrics=metrics)
        result = _metrics(pm, name)
        assert result['signal_eval']['trade_day_count'] == 1
        assert result['signal_eval']['observations'][0]['decision_ts_us'] == _ts('2026-07-06', 15)


@pytest.mark.parametrize('change', ['model_ref', 'train_dates', 'dataset', 'unordered_dates', 'missing_maturity'])
def test_invalid_training_identity_fails_without_publishing(daily_scenario: tuple[PathManager, dict[str, object], Mock], change: str) -> None:
    pm, fields, _ = daily_scenario
    model_ref = Path(fields['signal']['model_ref'])
    ref = model_ref.with_name('schedule.json')
    record = json.loads(ref.read_text())
    if change == 'model_ref':
        record['model_ref'] = str(model_ref.with_name('another.joblib'))
    elif change == 'train_dates':
        record['train_dates'] = ['2026-06-30']
    elif change == 'dataset':
        record['configuration']['dataset']['label_set'] = 'daily_close_return_rank_d3'
    elif change == 'unordered_dates':
        record['train_dates'] = ['2026-07-01', '2026-06-30']
    else:
        del record['label_maturity_ts_us']
    ref.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        submission = create_backtest_submission(mode='signal_eval', start='2026-07-03',
            end='2026-07-03', signal={'type': 'model', 'model_ref': str(model_ref)}, min_listing_calendar_days=120,
            strategy=fields['strategy'], initial_account=fields['initial_account'],
            execution={**fields['execution'], 'fill': {'type': 'quoted'}, 'risk_check_ts_us': []})
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name='backtest_2026-10-11_identity', metrics=metrics)
    assert not pm.experiment_dir(experiment_name='backtest_2026-10-11_identity').exists()


@pytest.mark.parametrize('failure', ['model', 'record', 'calendar', 'features'])
def test_input_failure_does_not_publish_success_artifacts(daily_scenario: tuple[PathManager, dict[str, object], Mock], backtest_log: Path, failure: str) -> None:
    pm, fields, access = daily_scenario
    model_ref = Path(fields['signal']['model_ref'])
    if failure == 'model':
        model_ref.unlink()
    elif failure == 'record':
        model_ref.with_name('schedule.json').unlink()
    elif failure == 'calendar':
        access.trade_dates.side_effect = RuntimeError('source unavailable')
    else:
        pm.feature_object(feature_set='tushare_daily_basic', version='v1', trade_date='2026-07-03').payload_path.unlink()
    with pytest.raises((FileNotFoundError, RuntimeError)):
        submission = create_backtest_submission(mode='signal_eval', start='2026-07-03',
            end='2026-07-03', signal={'type': 'model', 'model_ref': str(model_ref)}, min_listing_calendar_days=120,
            strategy=fields['strategy'], initial_account=fields['initial_account'],
            execution={**fields['execution'], 'fill': {'type': 'quoted'}, 'risk_check_ts_us': []})
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name='backtest_2026-10-11_failure', metrics=metrics)
    assert '✅ 回测报告' not in backtest_log.read_text()
    assert not pm.experiment_dir(experiment_name='backtest_2026-10-11_failure').exists()


def test_fact_write_failure_keeps_prior_files_and_new_run_uses_new_id(scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch) -> None:
    pm, fields, _ = scenario
    def write(*, output_file: Path, table: pa.Table) -> None:
        if output_file.name == 'equity.parquet':
            raise OSError('equity storage unavailable')
        write_parquet_atomic(output_file=output_file, table=table)
    monkeypatch.setattr('src.trading.backtest.steps.facts_persist.write_parquet_atomic', write)
    submission = create_backtest_submission(mode='full_backtest', **fields)
    with pytest.raises(OSError, match='equity storage unavailable'):
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name='backtest_2026-10-11_write-error', metrics=metrics)
    ref = pm.experiment_dir(experiment_name='backtest_2026-10-11_write-error')
    assert {p.name for p in ref.iterdir()} == {'execution.parquet'}
    original = (ref / 'execution.parquet').read_bytes()
    monkeypatch.setattr('src.trading.backtest.steps.facts_persist.write_parquet_atomic', write_parquet_atomic)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_write-retry', metrics=metrics)
    assert (ref / 'execution.parquet').read_bytes() == original
    assert pm.experiment_backtest_report(experiment_name='backtest_2026-10-11_write-retry').is_file()


@pytest.mark.parametrize('stage', ['risk', 'report'])
def test_rule_and_report_errors_preserve_the_original_exception(
    scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch,
    stage: str, backtest_log: Path,
) -> None:
    pm, fields, _ = scenario
    error = RuntimeError(f'{stage} unavailable')
    if stage == 'risk':
        monkeypatch.setattr('src.trading.risk.engine.RiskManager.apply', Mock(side_effect=error))
    else:
        monkeypatch.setattr('src.trading.backtest.reporting.bundle.ReportingBundle.run', Mock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        submission = create_backtest_submission(mode='full_backtest', **fields)
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name=f"backtest_2026-10-11_{stage}", metrics=metrics)
    assert caught.value is error
    name = f'backtest_2026-10-11_{stage}'
    assert not pm.experiment_backtest_report(experiment_name=name).exists()
    assert pm.experiment_backtest_metrics(experiment_name=name).exists() == (stage == 'report')
    assert pm.experiment_dir(experiment_name=name).exists() == (stage == 'report')
    expected_step = 'RiskDecisionStep' if stage == 'risk' else 'ReportStep'
    assert f'Pipeline 执行; scope={name} step={expected_step}' in caught.value.__notes__
    assert f'❌ 步骤耗时; scope={name}' in backtest_log.read_text()
    assert f'✅ 步骤耗时; scope={name}' not in backtest_log.read_text()
    assert '✅ 回测报告;' not in backtest_log.read_text()


@pytest.mark.parametrize(('mode', 'per_tick', 'final'), [
    ('signal_eval', ('AdvanceExecutionStep', 'AccountingStep', 'SignalStep', 'PortfolioStep',
                     'SubmitTargetsStep', 'AccountingStep'),
     ('FactsPersistStep', 'SignalEvaluationStep', 'ExecutionEvaluationStep', 'PortfolioEvaluationStep',
      'BacktestSummaryStep', 'MetricsPersistStep', 'ReportStep')),
    ('execution_eval', ('AdvanceExecutionStep', 'AccountingStep', 'RecordedOrdersStep', 'AccountingStep'),
     ('FactsPersistStep', 'ExecutionEvaluationStep', 'PortfolioEvaluationStep', 'BacktestSummaryStep', 'MetricsPersistStep', 'ReportStep')),
    ('strategy_eval', ('AdvanceExecutionStep', 'AccountingStep', 'SignalStep', 'PortfolioStep', 'SubmitTargetsStep', 'AccountingStep'),
     ('FactsPersistStep', 'SignalEvaluationStep', 'ExecutionEvaluationStep', 'PortfolioEvaluationStep', 'BacktestSummaryStep', 'MetricsPersistStep', 'ReportStep')),
    ('full_backtest', ('AdvanceExecutionStep', 'AccountingStep', 'SignalStep', 'PortfolioStep',
                       'RiskDecisionStep', 'SubmitTargetsStep', 'AccountingStep'),
     ('FactsPersistStep', 'SignalEvaluationStep', 'RiskMetricsStep', 'ExecutionEvaluationStep',
      'PortfolioEvaluationStep', 'BacktestSummaryStep', 'MetricsPersistStep', 'ReportStep')),
])
def test_single_account_modes_measure_the_complete_chain_before_success(
    scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch,
    backtest_log: Path, mode: BacktestMode, per_tick: tuple[str, ...], final: tuple[str, ...],
) -> None:
    pm, fields, _ = scenario
    submission = create_backtest_submission(mode='full_backtest', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_source', metrics=metrics)
    source = pm.experiment_dir(experiment_name='backtest_2026-10-11_source')
    name = 'backtest_2026-10-11_chain'
    measured: list[str] = []
    original_measure = Instrumentation.measure

    def measure[T](instrumentation: Instrumentation, operation_name: str,
                   operation: Callable[..., T], *args: object, **kwargs: object) -> T:
        measured.append(operation_name)
        if operation_name == ('FactsPersistStep' if per_tick else 'MetricsPersistStep'):
            assert not pm.experiment_dir(experiment_name=name).exists()
        if operation_name == 'SignalEvaluationStep':
            assert pm.experiment_backtest_execution(experiment_name=name).is_file()
            assert pm.experiment_backtest_equity(experiment_name=name).is_file()
        if operation_name == 'ReportStep':
            assert pm.experiment_backtest_metrics(experiment_name=name).is_file()
        return original_measure(instrumentation, operation_name, operation, *args, **kwargs)

    monkeypatch.setattr(Instrumentation, 'measure', measure)
    if mode == 'signal_eval':
        submission = create_backtest_submission(mode=mode,
            start=fields['start'], end=fields['end'], signal=fields['signal'],
            min_listing_calendar_days=fields['min_listing_calendar_days'],
            strategy=fields['strategy'], initial_account=fields['initial_account'],
            execution={**fields['execution'], 'fill': {'type': 'quoted'}, 'risk_check_ts_us': []})
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name='backtest_2026-10-11_chain', metrics=metrics)
    elif mode == 'execution_eval':
        execution = {**fields['execution'], 'risk_check_ts_us': []}
        submission = create_backtest_submission(mode=mode,
            start=fields['start'], end=fields['end'], order_refs=[str(source / 'orders.json')],
            initial_account=fields['initial_account'], execution=execution)
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name='backtest_2026-10-11_chain', metrics=metrics)
    elif mode == 'strategy_eval':
        reference = deepcopy(fields)
        reference.pop('risk')
        reference['strategy'].pop('quote')
        reference['execution']["fill"] = {"type": "reference"}
        reference['execution'].pop('risk_check_ts_us')
        submission = create_backtest_submission(mode=mode, **reference)
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name='backtest_2026-10-11_chain', metrics=metrics)
    else:
        submission = create_backtest_submission(mode=mode, **fields)
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name='backtest_2026-10-11_chain', metrics=metrics)

    tick_count = pq.read_table(pm.experiment_backtest_equity(experiment_name=name)).num_rows if per_tick else 0
    assert measured == list(per_tick) * tick_count + list(final)
    assert pm.experiment_backtest_report(experiment_name=name).is_file()
    log = backtest_log.read_text()
    assert log.count(f'✅ 步骤耗时; scope={name}') == 1
    assert log.rindex('✅ 回测报告;') < log.index(f'✅ 步骤耗时; scope={name}')


@pytest.mark.parametrize(('failure', 'step_name'), [
    ('prediction', 'SignalStep'), ('metrics', 'MetricsPersistStep'),
])
def test_signal_production_and_publication_failures_stop_the_measured_chain(
    signal_scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch,
    backtest_log: Path, failure: str, step_name: str,
) -> None:
    pm, fields, _ = signal_scenario
    inputs, _ = read_prediction_record(Path(fields['signal']['prediction_refs'][0]))
    fields['signal'] = {'type': 'model', 'model_ref': str(inputs.model_ref)}
    name = f'backtest_2026-10-11_{failure}'
    error = OSError(f'{failure} unavailable')
    if failure == 'prediction':
        monkeypatch.setattr('src.training.inference_model.InferenceModel.predict', Mock(side_effect=error))
    else:
        original_write = FileSystem.write_bytes_atomic

        def write(path: str | Path, data: bytes) -> None:
            if Path(path) == pm.experiment_backtest_metrics(experiment_name=name):
                raise error
            original_write(path, data)

        monkeypatch.setattr(FileSystem, 'write_bytes_atomic', write)
    with pytest.raises(OSError) as caught:
        submission = create_backtest_submission(mode='signal_eval', **fields)
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name=f"backtest_2026-10-11_{failure}", metrics=metrics)
    assert caught.value is error
    expected_notes = [f'Pipeline 执行; scope={name} step={step_name}']
    if failure == 'prediction':
        expected_notes.append(f"回测时点; trade_date=2026-07-03 ts_us={_ts('2026-07-03', 15)}")
    assert error.__notes__ == expected_notes
    assert not pm.experiment_backtest_metrics(experiment_name=name).exists()
    assert not pm.experiment_backtest_report(experiment_name=name).exists()
    predictions = list(pm.experiment_dir(experiment_name=name).glob('predictions/**/*.parquet'))
    assert len(predictions) == (1 if failure == 'metrics' else 0)
    assert pm.experiment_dir(experiment_name=name).exists() == (failure == 'metrics')
    assert f'❌ 步骤耗时; scope={name}' in backtest_log.read_text()
    assert f'✅ 步骤耗时; scope={name}' not in backtest_log.read_text()


def test_signal_workflow_publishes_the_returned_context_instead_of_initial_metrics(
    scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, fields, _ = scenario
    original_run = SignalEvaluationStep.run

    def produce(step: SignalEvaluationStep, context: BacktestResultContext) -> BacktestResultContext:
        context = original_run(step, context)
        return replace(context, metrics=context.metrics.model_copy(update={'not_run': {'risk': 'returned_context'}}))

    monkeypatch.setattr(SignalEvaluationStep, 'run', produce)
    submission = create_backtest_submission(mode='signal_eval',
        start=fields['start'], end=fields['end'], signal=fields['signal'],
        min_listing_calendar_days=fields['min_listing_calendar_days'],
        strategy=fields['strategy'], initial_account=fields['initial_account'],
        execution={**fields['execution'], 'fill': {'type': 'quoted'}, 'risk_check_ts_us': []})
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_returned', metrics=metrics)
    metrics = _metrics(pm, 'backtest_2026-10-11_returned')
    assert metrics['not_run'] == {'risk': 'returned_context'}
    assert metrics['signal_eval']['decision_count'] == 1
    assert metrics['prediction_refs'] == fields['signal']['prediction_refs']


@pytest.mark.parametrize("mode", ["signal_eval", "full_backtest"])
def test_future_labels_and_prices_cannot_change_prior_signal_target_or_order(
    scenario: tuple[PathManager, dict[str, object], Mock], mode: str,
) -> None:
    pm, fields, _ = scenario
    workflow = run_backtest
    if mode == "signal_eval":
        fields.pop("risk")
        fields["execution"]["fill"] = {"type": "quoted"}
        fields["execution"].pop("risk_check_ts_us")
        workflow = run_backtest
    before = None
    for run in ('before', 'after'):
        if run == 'after':
            ref = pm.label_object(label_set='daily_close_return_rank_d1', version='v1', trade_date='2026-07-03').payload_path
            labels = pd.read_parquet(ref)
            labels['y_rank_return'] = [np.nan, np.nan, np.nan]
            labels.to_parquet(ref, index=False)
            meta.commit(pm=pm, payload_path=ref)
            ref = Path(fields['execution']['market_ref'])
            market = json.loads(ref.read_text())
            for observation in market['observations']:
                observation['price'] = '11'
            ref.write_text(json.dumps(market))
        submission = create_backtest_submission(mode=mode, **fields)
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        workflow(submission=submission, path_manager=pm,
            experiment_name=f"backtest_2026-10-11_{run}", metrics=metrics)
        root = pm.experiment_dir(experiment_name=f'backtest_2026-10-11_{run}')
        facts = ((root / 'targets.json').read_bytes(), [cmd for cmd in json.loads((root / 'orders.json').read_text())
            if cmd['ts_us'] <= fields['initial_account']['ts_us']])
        result = _metrics(pm, root.name)
        if run == 'before':
            before = facts
            assert result['execution_eval']['filled_qty'] == (360 if mode == 'full_backtest' else 1000)
            assert result['signal_eval']['mean_ic'] == 1
        else:
            assert facts == before
            assert result['execution_eval']['filled_qty'] == (0 if mode == 'full_backtest' else 1000)
            assert result['signal_eval']['mean_ic'] is None


def test_two_minute_decisions_are_distinct_and_extra_wakeups_do_not_change_returns(scenario: tuple[PathManager, dict[str, object], Mock]) -> None:
    pm, fields, _ = scenario
    original = Path(fields['signal']['prediction_refs'][0])
    inputs, frame = read_prediction_record(original)
    model = load(inputs.model_ref)
    features = pd.read_parquet(pm.feature_object(feature_set=model.feature_set, version=model.feature_version,
                                                 trade_date='2026-07-03').payload_path)
    second = original.with_name('second.parquet')
    save_prediction_record(second, model_ref=inputs.model_ref, trade_date=inputs.trade_date,
        decision_ts_us=inputs.decision_ts_us + US_PER_MINUTE, features=features,
        feature_names=model.feature_names, predictions=frame['prediction'].to_numpy())
    fields['signal']['prediction_refs'].append(str(second))
    portfolios = []
    for run, wakeups in [('sparse', []), ('dense', [inputs.decision_ts_us + n * 1000000 for n in range(1, 40)])]:
        fields['execution']['risk_check_ts_us'] = wakeups
        submission = create_backtest_submission(mode='full_backtest', **fields)
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name=f"backtest_2026-10-11_{run}", metrics=metrics)
        result = _metrics(pm, f'backtest_2026-10-11_{run}')
        assert result['signal_eval']['decision_count'] == 2 and result['signal_eval']['trade_day_count'] == 1
        assert len(result['prediction_refs']) == 2
        portfolios.append(result['portfolio'])
    assert portfolios[0] == portfolios[1]


def test_strategy_and_cost_changes_affect_returns_without_changing_fixed_predictions(scenario: tuple[PathManager, dict[str, object], Mock]) -> None:
    from copy import deepcopy
    pm, original, _ = scenario
    prediction = Path(original['signal']['prediction_refs'][0])
    frozen = prediction.read_bytes()
    original_ic = evaluate_prediction_record(prediction, pm=pm)
    results = []
    for name, quantity, fee in [('small', 100, 0), ('large', 200, 0), ('fee', 200, 5)]:
        fields = deepcopy(original)
        fields.pop('risk')
        fields['strategy'].pop('quote')
        fields['strategy']['params']['target_quantity'] = quantity
        fields['execution']["fill"] = {"type": "reference"}
        fields['execution'].pop('risk_check_ts_us')
        fields['execution']['fees']['min_commission'] = fee
        submission = create_backtest_submission(mode='strategy_eval', **fields)
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
        run_backtest(submission=submission, path_manager=pm,
            experiment_name=f"backtest_2026-10-11_{name}", metrics=metrics)
        results.append(_metrics(pm, f'backtest_2026-10-11_{name}'))
    assert [r['final_account']['positions']['600000'] for r in results] == [100, 200, 200]
    assert [Decimal(r['portfolio']['total_fees']) for r in results] == [0, 0, 5]
    assert results[2]['portfolio']['total_return'] < results[1]['portfolio']['total_return']
    assert prediction.read_bytes() == frozen
    assert evaluate_prediction_record(prediction, pm=pm) == original_ic


def test_full_with_daily_execution_waits_for_the_subsequent_complete_day(daily_scenario: tuple[PathManager, dict[str, object], Mock]) -> None:
    pm, fields, _ = daily_scenario
    fields['end'] = '2026-07-03'
    fields['strategy']['order_window_minutes'] = 240
    ref = Path(fields['execution']['market_ref'])
    market = json.loads(ref.read_text())
    market['observations'] = []
    for day in ('2026-07-03', '2026-07-06'):
        for symbol in ('000001', '600000'):
            market['observations'].append({'observation_id': f'daily-{day}-{symbol}', 'symbol': symbol,
                'time': {'unit': 'trading_day', 'size': 1, 'trade_date': day, 'start_ts_us': _ts(day, 9, 30),
                    'end_ts_us': _ts(day, 15), 'available_ts_us': _ts(day, 15)}, 'price': '10', 'volume_qty': 100})
    ref.write_text(json.dumps(market))
    submission = create_backtest_submission(mode='full_backtest', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_daily-execution', metrics=metrics)
    root = pm.experiment_dir(experiment_name='backtest_2026-10-11_daily-execution')
    fills = [row for row in pq.read_table(root / 'execution.parquet').to_pylist() if row['event'] == 'FILL']
    assert len(fills) == 2 and all(row['ts_us'] == _ts('2026-07-06', 15) for row in fills)


def test_fake_live_batch_delivery_reproduces_full_signal_target_risk_and_accounts(daily_scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch) -> None:
    from itertools import groupby
    from src.trading.backtest.market.replay import ReplayMarket
    from src.trading.backtest.market.signals import ModelSignalBatch, RecordedSignalBatch, SignalBatch, produce_signal
    pm, fields, _ = daily_scenario
    submission = create_backtest_submission(mode='full_backtest', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_offline', metrics=metrics)
    source = ReplayMarketInput.model_validate_json(Path(fields['execution']['market_ref']).read_bytes())
    # The local live driver knows the schedule but releases each delivery batch only at availability.
    deliveries = iter((ts, tuple(group)) for ts, group in groupby(source.observations,
                                                                key=lambda o: o.time.available_ts_us))
    next_batch = next(deliveries, None)
    received = []
    def deliver(market: ReplayMarket, ts: int) -> tuple[ExecutionObservation, ...]:
        nonlocal next_batch
        batch = []
        if next_batch is not None and next_batch[0] == ts:
            for observation in next_batch[1]:
                batch.extend((observation,))
                received.append(observation.observation_id)
            next_batch = next(deliveries, None)
        assert next_batch is None or next_batch[0] > ts
        return tuple(batch)
    released_signals: list[int] = []
    current_ts_us: int | None = None
    on_time = ReplayMarket.on_time

    def advance(market: ReplayMarket, ts_us: int) -> None:
        nonlocal current_ts_us
        on_time(market, ts_us)
        current_ts_us = ts_us

    def predict_at_availability(batch: SignalBatch, *, prediction_dir: Path) -> RecordedSignalBatch:
        timestamp = batch.ts_us if isinstance(batch, ModelSignalBatch) else batch.event.ts_us
        assert timestamp == current_ts_us
        released_signals.append(timestamp)
        return produce_signal(batch, prediction_dir=prediction_dir)

    monkeypatch.setattr(ReplayMarket, 'on_time', advance)
    monkeypatch.setattr('src.trading.backtest.steps.backtest_layers.produce_signal', predict_at_availability)
    monkeypatch.setattr(ReplayMarket, 'observations_at', deliver)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode="json"), not_run={})
    run_backtest(submission=submission, path_manager=pm,
        experiment_name='backtest_2026-10-11_live', metrics=metrics)
    assert received == [o.observation_id for o in source.observations]
    assert released_signals == [_ts('2026-07-03', 15), _ts('2026-07-06', 15)]
    offline = pm.experiment_dir(experiment_name='backtest_2026-10-11_offline')
    live = pm.experiment_dir(experiment_name='backtest_2026-10-11_live')
    for artifact in ('orders.json', 'targets.json', 'risk-cases.json', 'execution.parquet', 'equity.parquet'):
        assert (offline / artifact).read_bytes() == (live / artifact).read_bytes()
    for original in (offline / 'predictions').rglob('*.parquet'):
        assert original.read_bytes() == (live / original.relative_to(offline)).read_bytes()
    before, after = _metrics(pm, offline.name), _metrics(pm, live.name)
    for key in ('risk_eval', 'execution_eval', 'portfolio', 'final_account'):
        assert before[key] == after[key]


@pytest.mark.parametrize('fill', ['quoted', 'reference', 'limit_price'])
def test_risk_comparison_replays_accounts_and_publishes_signed_differences(
    signal_scenario: tuple[PathManager, dict[str, object], Mock], fill: str,
    backtest_log: Path, mocker: MockerFixture,
) -> None:
    pm, fields, _ = signal_scenario
    fields['risk'] = {'rules': [{'type': 'max_single_name', 'max_ratio': 0.1}]}
    fields['execution']['fill'] = {'type': fill}
    if fill == 'reference':
        fields['strategy'].pop('quote')
    elif fill == 'limit_price':
        fields['execution']['fill']['slippage_bp'] = 0
    ref = Path(fields['execution']['market_ref'])
    market = json.loads(ref.read_text())
    for mark in market['marks'][1:]:
        mark['prices']['600000'] = '8'
    ref.write_text(json.dumps(market))
    submission = create_backtest_submission(mode='risk_rule_eval', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode='json'), not_run={})
    read_record = mocker.spy(signals, 'read_prediction_record')
    name = f'backtest_2026-10-11_compare-{fill}'
    read_bytes = mocker.spy(Path, 'read_bytes')

    run_backtest(submission=submission, path_manager=pm, experiment_name=name, metrics=metrics)

    assert sum(call.args == (ref,) for call in read_bytes.call_args_list) == 1
    root = pm.experiment_dir(experiment_name=name)
    baseline = json.loads((root / 'without_risk/metrics.json').read_text())
    treatment = json.loads((root / 'with_risk/metrics.json').read_text())
    comparison = _metrics(pm, name)['risk_comparison']
    baseline_qty = 360 if fill == 'limit_price' else 1000
    assert baseline['final_account']['positions'] == {'600000': baseline_qty}
    assert treatment['final_account']['positions'] == {'600000': 200}
    assert baseline['portfolio']['net_profit'] == -2 * baseline_qty
    assert treatment['portfolio']['net_profit'] == -400
    assert comparison['without_risk'] == baseline['portfolio']
    assert comparison['with_risk'] == treatment['portfolio']
    assert comparison['net_profit_delta'] == 2 * baseline_qty - 400
    assert comparison['total_return_delta'] == pytest.approx((2 * baseline_qty - 400) / 20000)
    assert comparison['max_drawdown_delta'] == pytest.approx((400 - 2 * baseline_qty) / 20000)
    assert Decimal(comparison['total_fees_delta']) == 0
    assert comparison['turnover_delta'] == pytest.approx(treatment['portfolio']['turnover'] - baseline['portfolio']['turnover'])
    assert baseline['signal_eval'] == treatment['signal_eval']
    assert baseline['prediction_refs'] == treatment['prediction_refs'] == fields['signal']['prediction_refs']
    assert baseline['applied_risk'] is None and treatment['applied_risk'] == fields['risk']
    assert 'risk_eval' not in baseline and treatment['risk_eval']['case_count'] > 0
    assert baseline['execution_eval']['filled_qty'] == baseline_qty
    assert treatment['execution_eval']['filled_qty'] == 200
    read_record.assert_called_once()
    for path in ('without_risk', 'with_risk'):
        for artifact in ('orders.json', 'targets.json', 'execution.parquet', 'equity.parquet', 'report.html'):
            assert (root / path / artifact).is_file()
    report = (root / 'report.html').read_text()
    assert '有／无风控对照' in report and 'without_risk/report.html' in report and 'with_risk/report.html' in report
    assert '-400.00' in report and f'{2 * baseline_qty - 400:+,.2f}' in report
    log = backtest_log.read_text()
    assert log.count('✅ 步骤耗时; scope=without_risk') == log.count('✅ 步骤耗时; scope=with_risk') == 1
    assert log.rindex('✅ 回测报告;') < log.index(f'✅ 步骤耗时; scope={name}')


def test_risk_comparison_uses_one_model_prediction_and_preserves_undefined_returns(
    signal_scenario: tuple[PathManager, dict[str, object], Mock], mocker: MockerFixture,
) -> None:
    from src.training.inference_model import InferenceModel
    pm, fields, _ = signal_scenario
    original, _ = read_prediction_record(Path(fields['signal']['prediction_refs'][0]))
    fields['signal'] = {'type': 'model', 'model_ref': str(original.model_ref)}
    fields['risk'] = {'rules': [{'type': 'max_single_name', 'max_ratio': 0.2}]}
    fields['initial_account'].update(cash='0', peak_equity=0)
    predict = mocker.spy(InferenceModel, 'predict')
    submission = create_backtest_submission(mode='risk_rule_eval', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode='json'), not_run={})
    name = 'backtest_2026-10-11_compare-model'

    run_backtest(submission=submission, path_manager=pm, experiment_name=name, metrics=metrics)

    predict.assert_called_once()
    root = pm.experiment_dir(experiment_name=name)
    baseline = json.loads((root / 'without_risk/metrics.json').read_text())
    treatment = json.loads((root / 'with_risk/metrics.json').read_text())
    assert baseline['prediction_refs'] == treatment['prediction_refs']
    assert len(list((root / 'predictions').rglob('*.parquet'))) == 1
    comparison = _metrics(pm, name)['risk_comparison']
    assert comparison['total_return_delta'] is None and comparison['turnover_delta'] is None
    for result in (baseline, treatment):
        assert result['portfolio']['net_profit'] == 0
        assert result['portfolio']['total_return'] is None
        assert result['portfolio']['undefined_reasons']
        assert result['final_account']['positions'] == {}


def test_risk_comparison_recalculates_strategy_targets_from_each_accounts_feedback(
    signal_scenario: tuple[PathManager, dict[str, object], Mock], tmp_path: Path,
) -> None:
    pm, fields, _ = signal_scenario
    record, _ = read_prediction_record(Path(fields['signal']['prediction_refs'][0]))
    model = load(record.model_ref)
    features = pd.read_parquet(pm.feature_object(feature_set=model.feature_set, version=model.feature_version,
                                                 trade_date='2026-07-03').payload_path)
    refs = []
    for minute, score in ((30, 0.9), (32, 0.5)):
        ref = tmp_path / f'comparison-{minute}.parquet'
        save_prediction_record(ref, model_ref=record.model_ref, trade_date='2026-07-03',
            decision_ts_us=_ts('2026-07-03', 14, minute), features=features,
            feature_names=model.feature_names,
            predictions=np.array([score if symbol == "600000" else 0.1 for symbol in features["symbol"]]))
        refs.append(str(ref))
    fields['signal']['prediction_refs'] = refs
    fields['strategy'] = {'type': 'topk_hysteresis', 'params': {'max_positions': 1,
        'entry_threshold': 0.8, 'exit_threshold': 0.2, 'keep_winners': True, 'target_quantity': 1000},
        'order_window_minutes': 2, 'quote': {'buy_offset_bp': 0, 'sell_offset_bp': 0}}
    fields['risk'] = {'rules': [{'type': 'max_single_name', 'max_ratio': 0.0}]}
    submission = create_backtest_submission(mode='risk_rule_eval', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode='json'), not_run={})
    name = 'backtest_2026-10-11_feedback'

    run_backtest(submission=submission, path_manager=pm, experiment_name=name, metrics=metrics)

    root = pm.experiment_dir(experiment_name=name)
    baseline = json.loads((root / 'without_risk/targets.json').read_text())
    treatment = json.loads((root / 'with_risk/targets.json').read_text())
    assert baseline[0] == treatment[0]
    assert baseline[1]['targets'] == {'600000': 1000, '000001': 0, '000002': 0}
    assert treatment[1]['targets'] == {'600000': 0, '000001': 0, '000002': 0}
    assert len(baseline) == len(treatment) == 2


def test_signal_and_strategy_modes_with_identical_assumptions_have_identical_metrics(
    signal_scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, _ = signal_scenario
    results = []
    for mode in ('signal_eval', 'strategy_eval'):
        submission = create_backtest_submission(mode=mode, **fields)
        metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode='json'), not_run={})
        run_backtest(submission=submission, path_manager=pm, experiment_name=mode, metrics=metrics)
        results.append(_metrics(pm, mode))
    for field in ('portfolio', 'signal_eval', 'execution_eval', 'final_account', 'not_run'):
        assert results[0][field] == results[1][field]


def test_risk_failure_keeps_baseline_facts_and_does_not_publish_a_comparison(
    signal_scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, fields, _ = signal_scenario
    fields['risk'] = {'rules': [{'type': 'max_single_name', 'max_ratio': 0.2}]}
    submission = create_backtest_submission(mode='risk_rule_eval', **fields)
    metrics = BacktestMetrics(mode=submission.mode, parameters=submission.model_dump(mode='json'), not_run={})
    error = ValueError('risk evaluation failed')
    monkeypatch.setattr('src.trading.risk.engine.RiskManager.apply', Mock(side_effect=error))
    name = 'backtest_2026-10-11_failed-comparison'

    with pytest.raises(ValueError) as caught:
        run_backtest(submission=submission, path_manager=pm, experiment_name=name, metrics=metrics)

    assert caught.value is error
    root = pm.experiment_dir(experiment_name=name)
    assert (root / 'without_risk/report.html').is_file()
    assert not (root / 'with_risk/metrics.json').exists()
    assert not (root / 'metrics.json').exists() and not (root / 'report.html').exists()
    assert 'scope=with_risk step=RiskDecisionStep' in error.__notes__[0]
