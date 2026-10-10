# filepath: tests/workflows/test_backtest.py
"""Fixed-model delivery through actual backtest composition and persisted replay."""

import json
import re
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

from src import logs
from src.access import Access, meta
from src.config.backtest_config import BacktestConfig, BacktestMode
from src.config.model_config import FeatureGroupConfig, MissingConfig, ModelPreset, PreprocessingConfig
from src.jobs.requests import SignalInput, create_backtest_submission, create_training_submission
from src.observability.instrumentation import Instrumentation
from src.training.evaluation import evaluate_prediction_record
from src.training.prediction import read_prediction_record, verify_prediction_record
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.filesystem import FileSystem
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import PathManager
from src.workflows.backtest import (run_signal_evaluation, run_risk_rule_evaluation, run_execution_evaluation,
    run_strategy_evaluation, run_full_backtest)
from src.training.artifact import load
from src.training.prediction import save_prediction_record
from src.trading.backtest.market.replay import ReplayMarketInput
from src.trading.backtest.context import SignalEvaluationContext
from src.trading.backtest.steps.backtest_layers import PredictionGenerationStep
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


def _logged_parameters(log_path: Path, *, experiment_name: str) -> dict[str, object]:
    lines = log_path.read_text().splitlines()
    assert lines[0] == f"▶️ 回测任务; experiment={experiment_name}"
    assert sum(line.startswith("▶️ 回测任务;") for line in lines) == 1
    parameters = {}
    for line in lines[1:]:
        match = re.fullmatch(r"[^ ]+ ([a-z_]+)=(.+)", line)
        if match is None:
            break
        parameters[match[1]] = json.loads(match[2])
    return parameters


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
        "start": day, "end": day,
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
                      "annual_factor": 252, "slippage_bp": 0, "risk_check_ts_us": [ts + 3 * US_PER_MINUTE]},
    }
    return pm, fields, access


def _metrics(pm: PathManager, name: str) -> dict[str, object]:
    return json.loads(pm.experiment_backtest_metrics(experiment_name=name).read_text())


def test_full_pipeline_closes_target_risk_partial_fills_and_expiry(
    scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, _ = scenario
    run_full_backtest(submission=create_backtest_submission(mode="full_backtest", **fields), path_manager=pm,
        experiment_id="full", backtest_config=BacktestConfig(min_listing_calendar_days=120))
    name = "backtest_2026-07-03_2026-07-03_full"
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
    pm, fields, _ = scenario
    run_full_backtest(submission=create_backtest_submission(mode="full_backtest", **fields), path_manager=pm,
        experiment_id="original", backtest_config=BacktestConfig(min_listing_calendar_days=120))
    original_name = "backtest_2026-07-03_2026-07-03_original"
    original = _metrics(pm, original_name)
    run_dir = pm.experiment_dir(experiment_name=original_name)
    run_signal_evaluation(submission=create_backtest_submission(mode="signal_eval",
        start=fields["start"], end=fields["end"], signal=fields["signal"]), path_manager=pm,
        experiment_id="signal", backtest_config=BacktestConfig(min_listing_calendar_days=120))
    signal = _metrics(pm, "backtest_2026-07-03_2026-07-03_signal")
    assert signal["signal_eval"] == original["signal_eval"]
    assert "final_account" not in signal and "execution_eval" not in signal
    assert not pm.experiment_backtest_execution(experiment_name="backtest_2026-07-03_2026-07-03_signal").exists()
    run_risk_rule_evaluation(submission=create_backtest_submission(mode="risk_rule_eval",
        case_refs=[str(run_dir / "risk-cases.json")], risk=fields["risk"]), path_manager=pm, experiment_id="risk")
    risk = _metrics(pm, "backtest_risk_risk")
    assert risk["mode"] == risk["parameters"]["mode"] == "risk_rule_eval"
    assert risk["risk_eval"] == original["risk_eval"]
    execution = dict(fields["execution"])
    execution["risk_check_ts_us"] = []
    run_execution_evaluation(submission=create_backtest_submission(mode="execution_eval",
        start=fields["start"], end=fields["end"], order_refs=[str(run_dir / "orders.json")],
        initial_account=fields["initial_account"], execution=execution), path_manager=pm, experiment_id="execution")
    replay = _metrics(pm, "backtest_2026-07-03_2026-07-03_execution")
    assert replay["execution_eval"] == original["execution_eval"]
    assert replay["final_account"] == original["final_account"]
    assert "signal_eval" not in replay and "risk_eval" not in replay


def test_reference_fills_are_account_constrained_and_ignore_quote_and_market_volume(
    scenario: tuple[PathManager, dict[str, object], Mock],
) -> None:
    pm, fields, _ = scenario
    fields.pop("risk")
    fields["strategy"].pop("quote")
    fields["execution"].pop("slippage_bp")
    fields["execution"].pop("risk_check_ts_us")
    fields["initial_account"]["cash"] = "5000"
    fields["initial_account"]["peak_equity"] = 5000
    run_strategy_evaluation(submission=create_backtest_submission(mode="strategy_eval", **fields),
        path_manager=pm, experiment_id="reference", backtest_config=BacktestConfig(min_listing_calendar_days=120))
    metrics = _metrics(pm, "backtest_2026-07-03_2026-07-03_reference")
    assert metrics["mode"] == metrics["parameters"]["mode"] == "strategy_eval"
    assert metrics["final_account"]["positions"] == {"600000": 500}
    assert metrics["final_account"]["cash"] == "0"
    assert "execution_eval" not in metrics and "risk_eval" not in metrics
    assert metrics["not_run"]["price_feasibility"] == "reference_fills"


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
    run_full_backtest(submission=create_backtest_submission(mode='full_backtest', **fields), path_manager=pm,
        experiment_id='scheduled', backtest_config=BacktestConfig(min_listing_calendar_days=120))
    run_dir = pm.experiment_dir(experiment_name='backtest_2026-07-03_2026-07-03_scheduled')
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


def test_daily_model_inference_preserves_rows_and_all_skipped_holdings(daily_scenario: tuple[PathManager, dict[str, object], Mock], backtest_log: Path) -> None:
    pm, fields, _ = daily_scenario
    feature_ref = pm.feature_object(feature_set='tushare_daily_basic', version='v1', trade_date='2026-07-06').payload_path
    features = pd.read_parquet(feature_ref)
    features['f_d_close_return_1d'] = np.nan
    features.to_parquet(feature_ref, index=False)
    meta.commit(pm=pm, payload_path=feature_ref)
    original_model = Path(fields['signal']['model_ref']).read_bytes()
    run_full_backtest(submission=create_backtest_submission(mode='full_backtest', **fields), path_manager=pm,
        experiment_id='daily', backtest_config=BacktestConfig(min_listing_calendar_days=120))
    name = 'backtest_2026-07-03_2026-07-06_daily'
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
    assert result['parameters']['backtest_config'] == {'min_listing_calendar_days': 120}
    assert _logged_parameters(backtest_log, experiment_name=name) == result['parameters']
    assert 'observations' not in backtest_log.read_text()


@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_model_maturity_boundary_precedes_first_selected_decision(delivery: tuple[PathManager, Path, Mock], offset: int) -> None:
    pm, model_ref, _ = delivery
    ref = model_ref.with_name('schedule.json')
    record = json.loads(ref.read_text())
    record['label_maturity_ts_us'] = _ts('2026-07-06', 15) + offset
    record['request'] = {'archived_intent': 'not a current request'}
    ref.write_text(json.dumps(record))
    submission = create_backtest_submission(mode='signal_eval', start='2026-07-04', end='2026-07-06',
        signal={'type': 'model', 'model_ref': str(model_ref)})
    name = 'backtest_2026-07-04_2026-07-06_maturity'
    if offset > 0:
        with pytest.raises(ValueError, match='label maturity'):
            run_signal_evaluation(submission=submission, path_manager=pm, experiment_id='maturity',
                                  backtest_config=BacktestConfig(min_listing_calendar_days=120))
        assert not pm.experiment_dir(experiment_name=name).exists()
    else:
        run_signal_evaluation(submission=submission, path_manager=pm, experiment_id='maturity',
                              backtest_config=BacktestConfig(min_listing_calendar_days=120))
        result = _metrics(pm, name)
        assert result['signal_eval']['trade_day_count'] == 1
        assert result['signal_eval']['observations'][0]['decision_ts_us'] == _ts('2026-07-06', 15)


@pytest.mark.parametrize('change', ['model_ref', 'train_dates', 'dataset', 'unordered_dates', 'missing_maturity'])
def test_invalid_training_identity_fails_without_publishing(delivery: tuple[PathManager, Path, Mock], change: str) -> None:
    pm, model_ref, _ = delivery
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
        run_signal_evaluation(submission=create_backtest_submission(mode='signal_eval', start='2026-07-03',
            end='2026-07-03', signal={'type': 'model', 'model_ref': str(model_ref)}), path_manager=pm,
            experiment_id='identity', backtest_config=BacktestConfig(min_listing_calendar_days=120))
    assert not pm.experiment_dir(experiment_name='backtest_2026-07-03_2026-07-03_identity').exists()


@pytest.mark.parametrize('failure', ['model', 'record', 'calendar', 'features'])
def test_input_failure_keeps_parameters_without_success_artifacts(delivery: tuple[PathManager, Path, Mock], backtest_log: Path, failure: str) -> None:
    pm, model_ref, access = delivery
    if failure == 'model':
        model_ref.unlink()
    elif failure == 'record':
        model_ref.with_name('schedule.json').unlink()
    elif failure == 'calendar':
        access.trade_dates.side_effect = RuntimeError('source unavailable')
    else:
        pm.feature_object(feature_set='tushare_daily_basic', version='v1', trade_date='2026-07-03').payload_path.unlink()
    with pytest.raises((FileNotFoundError, RuntimeError)):
        run_signal_evaluation(submission=create_backtest_submission(mode='signal_eval', start='2026-07-03',
            end='2026-07-03', signal={'type': 'model', 'model_ref': str(model_ref)}), path_manager=pm,
            experiment_id='failure', backtest_config=BacktestConfig(min_listing_calendar_days=120))
    assert '信号输入 signal=' in backtest_log.read_text()
    assert '✅ 回测报告' not in backtest_log.read_text()
    assert not pm.experiment_dir(experiment_name='backtest_2026-07-03_2026-07-03_failure').exists()


def test_fact_write_failure_keeps_prior_files_and_rejects_overwrite(scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch) -> None:
    pm, fields, _ = scenario
    def write(*, output_file: Path, table: pa.Table) -> None:
        if output_file.name == 'equity.parquet':
            raise OSError('equity storage unavailable')
        write_parquet_atomic(output_file=output_file, table=table)
    monkeypatch.setattr('src.trading.backtest.steps.facts_persist.write_parquet_atomic', write)
    submission = create_backtest_submission(mode='full_backtest', **fields)
    with pytest.raises(OSError, match='equity storage unavailable'):
        run_full_backtest(submission=submission, path_manager=pm, experiment_id='write-error',
                          backtest_config=BacktestConfig(min_listing_calendar_days=120))
    ref = pm.experiment_dir(experiment_name='backtest_2026-07-03_2026-07-03_write-error')
    assert {p.name for p in ref.iterdir()} == {'execution.parquet'}
    original = (ref / 'execution.parquet').read_bytes()
    with pytest.raises(FileExistsError, match='experiment already exists'):
        run_full_backtest(submission=submission, path_manager=pm, experiment_id='write-error',
                          backtest_config=BacktestConfig(min_listing_calendar_days=120))
    assert (ref / 'execution.parquet').read_bytes() == original


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
        run_full_backtest(submission=create_backtest_submission(mode='full_backtest', **fields), path_manager=pm,
            experiment_id=stage, backtest_config=BacktestConfig(min_listing_calendar_days=120))
    assert caught.value is error
    name = f'backtest_2026-07-03_2026-07-03_{stage}'
    assert not pm.experiment_backtest_report(experiment_name=name).exists()
    assert pm.experiment_backtest_metrics(experiment_name=name).exists() == (stage == 'report')
    expected_step = 'RiskDecisionStep' if stage == 'risk' else 'ReportStep'
    assert f'Pipeline 执行; scope={name} step={expected_step}' in caught.value.__notes__
    assert f'❌ 步骤耗时; scope={name}' in backtest_log.read_text()
    assert f'✅ 步骤耗时; scope={name}' not in backtest_log.read_text()
    assert '✅ 回测报告;' not in backtest_log.read_text()


@pytest.mark.parametrize(('mode', 'per_tick', 'final'), [
    ('signal_eval', (), ('PredictionGenerationStep', 'SignalEvaluationStep', 'MetricsPersistStep', 'ReportStep')),
    ('risk_rule_eval', (), ('RiskEvaluationStep', 'RiskMetricsStep', 'MetricsPersistStep', 'ReportStep')),
    ('execution_eval', ('AdvanceExecutionStep', 'AccountingStep', 'RecordedOrdersStep'),
     ('FactsPersistStep', 'ExecutionEvaluationStep', 'BacktestSummaryStep', 'MetricsPersistStep', 'ReportStep')),
    ('strategy_eval', ('AdvanceExecutionStep', 'AccountingStep', 'SignalStep', 'PortfolioStep', 'SubmitTargetsStep'),
     ('FactsPersistStep', 'PortfolioEvaluationStep', 'BacktestSummaryStep', 'MetricsPersistStep', 'ReportStep')),
    ('full_backtest', ('AdvanceExecutionStep', 'AccountingStep', 'SignalStep', 'PortfolioStep',
                       'RiskDecisionStep', 'SubmitTargetsStep'),
     ('FactsPersistStep', 'SignalEvaluationStep', 'RiskMetricsStep', 'ExecutionEvaluationStep',
      'PortfolioEvaluationStep', 'BacktestSummaryStep', 'MetricsPersistStep', 'ReportStep')),
])
def test_every_mode_measures_its_complete_chain_before_success(
    scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch,
    backtest_log: Path, mode: BacktestMode, per_tick: tuple[str, ...], final: tuple[str, ...],
) -> None:
    pm, fields, _ = scenario
    config = BacktestConfig(min_listing_calendar_days=120)
    run_full_backtest(submission=create_backtest_submission(mode='full_backtest', **fields),
                      path_manager=pm, experiment_id='source', backtest_config=config)
    source = pm.experiment_dir(experiment_name='backtest_2026-07-03_2026-07-03_source')
    name = 'backtest_risk_chain' if mode == 'risk_rule_eval' else 'backtest_2026-07-03_2026-07-03_chain'
    measured: list[str] = []
    original_measure = Instrumentation.measure

    def measure[T](instrumentation: Instrumentation, operation_name: str,
                   operation: Callable[..., T], *args: object, **kwargs: object) -> T:
        measured.append(operation_name)
        if mode == 'full_backtest' and operation_name == 'SignalEvaluationStep':
            assert pm.experiment_backtest_execution(experiment_name=name).is_file()
            assert pm.experiment_backtest_equity(experiment_name=name).is_file()
        if operation_name == 'ReportStep':
            assert pm.experiment_backtest_metrics(experiment_name=name).is_file()
        return original_measure(instrumentation, operation_name, operation, *args, **kwargs)

    monkeypatch.setattr(Instrumentation, 'measure', measure)
    if mode == 'signal_eval':
        run_signal_evaluation(submission=create_backtest_submission(mode=mode,
            start=fields['start'], end=fields['end'], signal=fields['signal']),
            path_manager=pm, experiment_id='chain', backtest_config=config)
    elif mode == 'risk_rule_eval':
        run_risk_rule_evaluation(submission=create_backtest_submission(mode=mode,
            case_refs=[str(source / 'risk-cases.json')], risk=fields['risk']),
            path_manager=pm, experiment_id='chain')
    elif mode == 'execution_eval':
        execution = {**fields['execution'], 'risk_check_ts_us': []}
        run_execution_evaluation(submission=create_backtest_submission(mode=mode,
            start=fields['start'], end=fields['end'], order_refs=[str(source / 'orders.json')],
            initial_account=fields['initial_account'], execution=execution),
            path_manager=pm, experiment_id='chain')
    elif mode == 'strategy_eval':
        reference = deepcopy(fields)
        reference.pop('risk')
        reference['strategy'].pop('quote')
        reference['execution'].pop('slippage_bp')
        reference['execution'].pop('risk_check_ts_us')
        run_strategy_evaluation(submission=create_backtest_submission(mode=mode, **reference),
            path_manager=pm, experiment_id='chain', backtest_config=config)
    else:
        run_full_backtest(submission=create_backtest_submission(mode=mode, **fields),
            path_manager=pm, experiment_id='chain', backtest_config=config)

    tick_count = pq.read_table(pm.experiment_backtest_equity(experiment_name=name)).num_rows if per_tick else 0
    assert measured == list(per_tick) * tick_count + list(final)
    assert pm.experiment_backtest_report(experiment_name=name).is_file()
    log = backtest_log.read_text()
    assert log.count(f'✅ 步骤耗时; scope={name}') == 1
    assert log.rindex('✅ 回测报告;') < log.index(f'✅ 步骤耗时; scope={name}')


@pytest.mark.parametrize(('failure', 'step_name'), [
    ('prediction', 'PredictionGenerationStep'), ('metrics', 'MetricsPersistStep'),
])
def test_signal_production_and_publication_failures_stop_the_measured_chain(
    delivery: tuple[PathManager, Path, Mock], monkeypatch: pytest.MonkeyPatch,
    backtest_log: Path, failure: str, step_name: str,
) -> None:
    pm, model_ref, _ = delivery
    name = f'backtest_2026-07-03_2026-07-03_{failure}'
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
        run_signal_evaluation(submission=create_backtest_submission(mode='signal_eval',
            start='2026-07-03', end='2026-07-03', signal={'type': 'model', 'model_ref': str(model_ref)}),
            path_manager=pm, experiment_id=failure, backtest_config=BacktestConfig(min_listing_calendar_days=120))
    assert caught.value is error
    assert error.__notes__ == [f'Pipeline 执行; scope={name} step={step_name}']
    assert not pm.experiment_backtest_metrics(experiment_name=name).exists()
    assert not pm.experiment_backtest_report(experiment_name=name).exists()
    predictions = list(pm.experiment_dir(experiment_name=name).glob('predictions/**/*.parquet'))
    assert len(predictions) == (1 if failure == 'metrics' else 0)
    assert f'❌ 步骤耗时; scope={name}' in backtest_log.read_text()
    assert f'✅ 步骤耗时; scope={name}' not in backtest_log.read_text()


def test_signal_workflow_publishes_the_returned_context_instead_of_initial_metrics(
    scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, fields, _ = scenario
    original_run = PredictionGenerationStep.run

    def produce(step: PredictionGenerationStep, context: SignalEvaluationContext) -> SignalEvaluationContext:
        context = original_run(step, context)
        return replace(context, metrics=context.metrics.model_copy(update={'not_run': {'risk': 'returned_context'}}))

    monkeypatch.setattr(PredictionGenerationStep, 'run', produce)
    run_signal_evaluation(submission=create_backtest_submission(mode='signal_eval',
        start=fields['start'], end=fields['end'], signal=fields['signal']), path_manager=pm,
        experiment_id='returned', backtest_config=BacktestConfig(min_listing_calendar_days=120))
    metrics = _metrics(pm, 'backtest_2026-07-03_2026-07-03_returned')
    assert metrics['not_run'] == {'risk': 'returned_context'}
    assert metrics['signal_eval']['decision_count'] == 1
    assert metrics['prediction_refs'] == fields['signal']['prediction_refs']


def test_future_labels_and_prices_cannot_change_prior_signal_target_or_order(scenario: tuple[PathManager, dict[str, object], Mock]) -> None:
    pm, fields, _ = scenario
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
        run_full_backtest(submission=create_backtest_submission(mode='full_backtest', **fields), path_manager=pm,
            experiment_id=run, backtest_config=BacktestConfig(min_listing_calendar_days=120))
        root = pm.experiment_dir(experiment_name=f'backtest_2026-07-03_2026-07-03_{run}')
        facts = ((root / 'targets.json').read_bytes(), [cmd for cmd in json.loads((root / 'orders.json').read_text())
            if cmd['ts_us'] <= fields['initial_account']['ts_us']])
        result = _metrics(pm, root.name)
        if run == 'before':
            before = facts
            assert result['execution_eval']['filled_qty'] == 360
            assert result['signal_eval']['mean_ic'] == 1
        else:
            assert facts == before
            assert result['execution_eval']['filled_qty'] == 0
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
        run_full_backtest(submission=create_backtest_submission(mode='full_backtest', **fields), path_manager=pm,
            experiment_id=run, backtest_config=BacktestConfig(min_listing_calendar_days=120))
        result = _metrics(pm, f'backtest_2026-07-03_2026-07-03_{run}')
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
        fields['execution'].pop('slippage_bp')
        fields['execution'].pop('risk_check_ts_us')
        fields['execution']['fees']['min_commission'] = fee
        run_strategy_evaluation(submission=create_backtest_submission(mode='strategy_eval', **fields),
            path_manager=pm, experiment_id=name, backtest_config=BacktestConfig(min_listing_calendar_days=120))
        results.append(_metrics(pm, f'backtest_2026-07-03_2026-07-03_{name}'))
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
    run_full_backtest(submission=create_backtest_submission(mode='full_backtest', **fields), path_manager=pm,
        experiment_id='daily-execution', backtest_config=BacktestConfig(min_listing_calendar_days=120))
    root = pm.experiment_dir(experiment_name='backtest_2026-07-03_2026-07-03_daily-execution')
    fills = [row for row in pq.read_table(root / 'execution.parquet').to_pylist() if row['event'] == 'FILL']
    assert len(fills) == 2 and all(row['ts_us'] == _ts('2026-07-06', 15) for row in fills)


def test_fake_live_batch_delivery_reproduces_full_signal_target_risk_and_accounts(daily_scenario: tuple[PathManager, dict[str, object], Mock], monkeypatch: pytest.MonkeyPatch) -> None:
    from itertools import groupby
    from src.trading.backtest.market.replay import ReplayMarket
    from src.trading.backtest.market.signals import SignalBatch, prepare_signals
    pm, fields, _ = daily_scenario
    submission = create_backtest_submission(mode='full_backtest', **fields)
    run_full_backtest(submission=submission, path_manager=pm, experiment_id='offline',
                      backtest_config=BacktestConfig(min_listing_calendar_days=120))
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
    def prepare_live(
        signal: SignalInput, *, start: str, end: str, access: Access,
        pm: PathManager, min_listing_calendar_days: int,
    ) -> dict[int, SignalBatch]:
        batches = prepare_signals(signal, start=start, end=end, access=access, pm=pm,
                                  min_listing_calendar_days=min_listing_calendar_days)
        deliveries = iter(batches.items())
        ready = next(deliveries, None)
        class DeliveredSignals(dict[int, SignalBatch]):
            def get(self, timestamp: int, default: SignalBatch | None = None) -> SignalBatch | None:
                nonlocal ready
                if ready is None or timestamp < ready[0]:
                    return default
                assert timestamp == ready[0]
                released_signals.append(timestamp)
                batch = ready[1]
                ready = next(deliveries, None)
                return batch
        return DeliveredSignals(batches)
    monkeypatch.setattr('src.workflows.backtest.prepare_signals', prepare_live)
    monkeypatch.setattr(ReplayMarket, 'observations_at', deliver)
    run_full_backtest(submission=submission, path_manager=pm, experiment_id='live',
                      backtest_config=BacktestConfig(min_listing_calendar_days=120))
    assert received == [o.observation_id for o in source.observations]
    assert released_signals == [_ts('2026-07-03', 15), _ts('2026-07-06', 15)]
    offline = pm.experiment_dir(experiment_name='backtest_2026-07-03_2026-07-06_offline')
    live = pm.experiment_dir(experiment_name='backtest_2026-07-03_2026-07-06_live')
    for artifact in ('orders.json', 'targets.json', 'risk-cases.json', 'execution.parquet', 'equity.parquet'):
        assert (offline / artifact).read_bytes() == (live / artifact).read_bytes()
    for original in (offline / 'predictions').rglob('*.parquet'):
        assert original.read_bytes() == (live / original.relative_to(offline)).read_bytes()
    before, after = _metrics(pm, offline.name), _metrics(pm, live.name)
    for key in ('risk_eval', 'execution_eval', 'portfolio', 'final_account'):
        assert before[key] == after[key]
