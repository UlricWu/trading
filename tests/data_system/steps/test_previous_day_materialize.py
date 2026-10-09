# filepath: tests/data_system/steps/test_previous_day_materialize.py
"""Publish aligned preopen inputs while preserving original ranking universes."""

from datetime import date, time
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.access import Access, meta
from src.access.feature_label import read_features, read_labels
from src.data_system.builders.previous_day import DAILY_SOURCE_COLUMNS, PREOPEN_FEATURE_COLUMNS
from src.data_system.builders.stock_1430 import STOCK_1430_FEATURE_SCHEMA
from src.data_system.context import DataContext
from src.data_system.steps.previous_day_materialize import PreviousDayMaterializeStep
from src.utils.datetime_utils import DateTimeUtils
from src.utils.parquet_writer import write_parquet_atomic
from src.utils.path import ObjectPaths, PathManager


@pytest.mark.parametrize("wrong_input_date", [False, True])
def test_publish_ranks_before_qualification_and_reuse_committed_partitions(tmp_path: Path, wrong_input_date: bool) -> None:
    pm = PathManager(tmp_path)
    previous, target, following = "2026-07-02", "2026-07-03", "2026-07-06"
    symbols = ["000001", "000002", "600001"]
    objects: list[tuple[ObjectPaths, pa.Table]] = []
    calendar = pm.processed_year_object(dataset_name="trade_calendar", version="v1", calendar_year=2026)
    objects.append((calendar, pa.table({"trade_date": [previous, target, following], "is_open": [True] * 3})))
    for day in (previous, target):
        frame = pd.DataFrame({"symbol": symbols, "trade_date": [day] * 3,
            "decision_ts_utc": [DateTimeUtils.local_time_to_utc_epoch_us(time(14, 30), date.fromisoformat(day))] * 3,
            **{name: [0.1, 0.5, 0.9] for name in STOCK_1430_FEATURE_SCHEMA.names[3:]}})
        objects.append((pm.feature_object(feature_set="l2_stock_1430", version="v1", trade_date=day), pa.Table.from_pandas(frame, preserve_index=False)))
    daily = pa.table({"symbol": symbols, "trade_date": [previous] * 3, **{name: [10.0, 20.0, 30.0] for name in DAILY_SOURCE_COLUMNS}})
    objects.append((pm.feature_object(feature_set="tushare_daily_basic", version="v1", trade_date=previous), daily))
    statuses = {
        "stock_basic": pa.table({"symbol": symbols, "trade_date": [previous] * 3, "list_date": ["2020-01-01"] * 3}),
        "stock_st": pa.table({"symbol": ["600001"], "trade_date": [target if wrong_input_date else previous]}),
        "suspend_d": pa.table({name: pa.array([], type=pa.string()) for name in ("symbol", "trade_date", "suspend_type", "suspend_timing")}),
    }
    objects.extend((pm.processed_object(dataset_name=name, version="v1", trade_date=previous), table) for name, table in statuses.items())
    for day in (target, following):
        for market, indexes in (("sh", [2]), ("sz", [0, 1])):
            table = pa.table({"symbol": [symbols[i] for i in indexes], "trade_date": [day] * len(indexes),
                "phase": pa.array([2] * len(indexes), type=pa.int8()),
                "minute_start_ts_utc": [DateTimeUtils.local_time_to_utc_epoch_us(time(14, 31), date.fromisoformat(day))] * len(indexes),
                "notional_sum": [100.0] * len(indexes), "volume_sum": [10] * len(indexes),
                "high": [11.0 + i for i in indexes]})
            objects.append((pm.processed_object(dataset_name=f"{market}_stock_trade_1m", version="v1", trade_date=day), table))
        factors = pa.table({"symbol": symbols, "trade_date": [day] * 3, "adj_factor": [1.0] * 3})
        objects.append((pm.processed_object(dataset_name="adj_factor", version="v1", trade_date=day), factors))
    for paths, table in objects:
        write_parquet_atomic(output_file=paths.payload_path, table=table)
        meta.commit(pm=pm, payload_path=paths.payload_path)
    step = PreviousDayMaterializeStep(pm=pm, access=Access(pm=pm, processed_version="v1"))
    context = DataContext(start=target, end=target, trade_dates=(target,))
    if wrong_input_date:
        with pytest.raises(ValueError, match="input partition date mismatch"):
            step.run(context)
        assert not pm.feature_object(feature_set="previous_day_daily_l2", version="v1", trade_date=target).payload_path.exists()
        return
    assert step.run(context) is context
    features = read_features(pm=pm, feature_set="previous_day_daily_l2", version="v1", trade_date=target, feature_names=PREOPEN_FEATURE_COLUMNS)
    labels = read_labels(pm=pm, label_set="next_day_high_return_rank", version="v1", trade_date=target, label_column="y_rank_return")
    assert features.symbol.tolist() == labels.symbol.tolist() == symbols[:2]
    np.testing.assert_array_equal(features.lag_f_d_log_amount_rank, [1 / 3, 2 / 3])
    np.testing.assert_array_equal(labels.y_rank_return, [1 / 3, 2 / 3])
    output = pm.feature_object(feature_set="previous_day_daily_l2", version="v1", trade_date=target).payload_path
    lineage = json.loads(pq.read_schema(output).metadata[b"source_inputs"])
    assert lineage['historical_received_at'] is None
    assert len(lineage['inputs']) == 10
    for item in lineage['inputs']:
        path = pm.storage_root / item['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
    old_bytes = output.read_bytes()
    for paths, _ in objects:
        paths.payload_path.unlink()
        paths.meta_path.unlink()
    assert step.run(context) is context
    assert output.read_bytes() == old_bytes
