# filepath: src/data_system/steps/stock_1430_daily_l2_materialize.py
"""Materialize fixed H04 daily/L2 Feature partitions from committed inputs."""

from __future__ import annotations

from datetime import date
from functools import partial

import pyarrow as pa
import pyarrow.parquet as pq

from src import logs
from src.access import Access, meta
from src.data_system.builders.stock_1430_daily_l2 import (
    STOCK_1430_DAILY_SOURCE_COLUMNS,
    build_stock_1430_daily_l2_features,
)
from src.data_system.context import DataContext
from src.data_system.steps._derived_partition import _publish_derived_partition
from src.utils.path import PathManager

_FEATURE_SET = "stock_1430_daily_l2"
_VERSION = "v1"


class Stock1430DailyL2MaterializeStep:
    """Reuse or publish the fixed H04 Feature over resolved target sessions.

    Example:
        step = Stock1430DailyL2MaterializeStep(pm=path_manager, access=access)
        step.run(
            DataContext(
                start="2026-05-06", end="2026-05-06",
                trade_dates=("2026-05-06",),
            )
        )
    """

    def __init__(self, *, pm: PathManager, access: Access) -> None:
        """Bind the output store and its formal calendar Access.

        Example:
            step = Stock1430DailyL2MaterializeStep(pm=path_manager, access=access)
        """
        self._pm = pm
        self._access = access

    def run(self, context: DataContext) -> DataContext:
        """Reuse or publish each target in order, preserving earlier successes.

        Example:
            next_context = step.run(
                DataContext(
                    start="2026-05-06", end="2026-05-06",
                    trade_dates=("2026-05-06",),
                )
            )
        """
        for trade_date in context.trade_dates:
            paths = self._pm.feature_object(
                feature_set=_FEATURE_SET, version=_VERSION, trade_date=trade_date
            )
            who = f"stock 14:30 daily/L2 Feature; trade_date={trade_date} version={_VERSION}"
            rows = _publish_derived_partition(
                pm=self._pm,
                meta_path=paths.meta_path,
                output_path=paths.payload_path,
                who=who,
                build=partial(self._build, trade_date=trade_date),
            )
            if rows is None:
                logs.info(f"♻️ {who}")
            else:
                logs.info(f"✅ {who} rows={rows}")
        return context

    def _build(self, *, trade_date: str) -> pa.Table:
        previous_trade_date, _ = self._access.recent_trade_dates(
            end_date=trade_date, sessions=2
        )
        l2_paths = self._pm.feature_object(
            feature_set="l2_stock_1430", version="v1", trade_date=trade_date
        )
        daily_paths = self._pm.feature_object(
            feature_set="tushare_daily_basic",
            version="v1",
            trade_date=previous_trade_date,
        )
        l2_record = meta.require(
            pm=self._pm,
            meta_path=l2_paths.meta_path,
            expected_payload_path=l2_paths.payload_path,
        )
        daily_record = meta.require(
            pm=self._pm,
            meta_path=daily_paths.meta_path,
            expected_payload_path=daily_paths.payload_path,
        )
        for record in (l2_record, daily_record):
            if record.upstream is not None or record.symbol_slices is not None:
                raise RuntimeError(
                    f"Feature input Meta must not contain relationship fields: "
                    f"payload={record.payload_path}"
                )
        with pq.ParquetFile(l2_record.payload_path) as parquet_file:
            l2_features = parquet_file.read()
        with pq.ParquetFile(daily_record.payload_path) as parquet_file:
            daily_features = parquet_file.read(
                columns=["symbol", "trade_date", *STOCK_1430_DAILY_SOURCE_COLUMNS]
            )
        return build_stock_1430_daily_l2_features(
            l2_features,
            daily_features,
            trade_date=date.fromisoformat(trade_date),
            previous_trade_date=date.fromisoformat(previous_trade_date),
        )
