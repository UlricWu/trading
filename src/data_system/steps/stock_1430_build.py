# filepath: src/data_system/steps/stock_1430_build.py
"""Publish the fixed V1 14:30 Level-2 Feature and T+1 Label pair."""

from __future__ import annotations

from datetime import date
from functools import partial

import pyarrow as pa
import pyarrow.parquet as pq

from src import logs
from src.access import Access, meta
from src.data_system.builders.stock_1430 import (
    STOCK_1430_KEY_SCHEMA,
    build_stock_1430_features,
    build_stock_1430_labels,
)
from src.data_system.context import DataContext
from src.data_system.steps._partition import _publish_partition
from src.utils.path import ObjectPaths, PathManager

_FEATURE_SET = "l2_stock_1430"
_LABEL_SET = "l2_stock_1430_t1_vwap_rank"
_VERSION = "v1"


class Stock1430BuildStep:
    """Build the fixed H03 Feature then Label for each target session.

    Example:
        step = Stock1430BuildStep(pm=path_manager, access=access)
        step.run(
            DataContext(
                start="2026-05-06",
                end="2026-05-06",
                trade_dates=("2026-05-06",),
            )
        )
    """

    def __init__(self, *, pm: PathManager, access: Access) -> None:
        """Bind the formal store and its fixed V1 processed-data Access.

        Example:
            step = Stock1430BuildStep(pm=path_manager, access=access)
        """
        self._pm = pm
        self._access = access

    def run(self, context: DataContext) -> DataContext:
        """Build target dates in Context order, Feature before Label.

        Example:
            next_context = step.run(
                DataContext(
                    start="2026-05-06",
                    end="2026-05-06",
                    trade_dates=("2026-05-06",),
                )
            )
        """
        for trade_date in context.trade_dates:
            session = date.fromisoformat(trade_date)
            feature_paths = self._pm.feature_object(
                feature_set=_FEATURE_SET,
                version=_VERSION,
                trade_date=trade_date,
            )
            feature_who = (
                f"stock 14:30 Feature; trade_date={trade_date} version={_VERSION}"
            )

            def _build_feature(session: date = session) -> pa.Table:
                return build_stock_1430_features(
                    self._access.stock_trade_minutes(trade_date=session.isoformat()),
                    trade_date=session,
                )

            feature_rows = _publish_partition(
                pm=self._pm,
                paths=feature_paths,
                who=feature_who,
                build=_build_feature,
            )
            if feature_rows is None:
                logs.info(f"♻️ {feature_who}")
            else:
                logs.info(f"✅ {feature_who} rows={feature_rows}")

            label_paths = self._pm.label_object(
                label_set=_LABEL_SET,
                version=_VERSION,
                trade_date=trade_date,
            )
            label_who = f"stock 14:30 Label; trade_date={trade_date} version={_VERSION}"
            label_rows = _publish_partition(
                pm=self._pm,
                paths=label_paths,
                who=label_who,
                build=partial(
                    self._build_label,
                    trade_date=session,
                    feature_paths=feature_paths,
                ),
            )
            if label_rows is None:
                logs.info(f"♻️ {label_who}")
            else:
                logs.info(f"✅ {label_who} rows={label_rows}")
        return context

    def _build_label(
        self,
        *,
        trade_date: date,
        feature_paths: ObjectPaths,
    ) -> pa.Table:
        feature_record = meta.require(
            pm=self._pm,
            meta_path=feature_paths.meta_path,
            expected_payload_path=feature_paths.payload_path,
        )
        with pq.ParquetFile(feature_record.payload_path) as parquet_file:
            feature_keys = parquet_file.read(columns=STOCK_1430_KEY_SCHEMA.names)
        date_text = trade_date.isoformat()
        next_trade_date = self._access.next_trade_date(trade_date=date_text)
        return build_stock_1430_labels(
            feature_keys=feature_keys,
            entry_minutes=self._access.stock_trade_minutes(trade_date=date_text),
            exit_minutes=self._access.stock_trade_minutes(trade_date=next_trade_date),
            entry_factors=self._access.adjustment_factors(trade_date=date_text),
            exit_factors=self._access.adjustment_factors(trade_date=next_trade_date),
            trade_date=trade_date,
            next_trade_date=date.fromisoformat(next_trade_date),
        )
