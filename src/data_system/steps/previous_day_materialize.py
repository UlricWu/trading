# filepath: src/data_system/steps/previous_day_materialize.py
"""Materialize qualified preopen inputs from committed market objects."""

from datetime import date
import hashlib
import json

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src import logs
from src.access import Access, meta
from src.data_system.builders.previous_day import (
    PREOPEN_AVAILABLE_TIME, build_next_day_high_labels, build_previous_day_features, qualify_previous_day,
)
from src.data_system.context import DataContext
from src.data_system.steps._partition import _publish_parquet_object
from src.utils.datetime_utils import DateTimeUtils
from src.utils.path import ObjectPaths, PathManager


class PreviousDayMaterializeStep:
    """Build independent preopen Feature, eligibility, rank and event partitions.

    Example:
        step = PreviousDayMaterializeStep(pm=pm, access=access)
        step.run(DataContext(start="2026-07-01", end="2026-07-01", trade_dates=("2026-07-01",)))
    """

    def __init__(self, *, pm: PathManager, access: Access) -> None:
        """Bind committed sources and the formal calendar.

        Example:
            step = PreviousDayMaterializeStep(pm=pm, access=access)
        """
        self._pm = pm
        self._access = access

    def run(self, context: DataContext) -> DataContext:
        """Publish missing objects; existing valid objects remain immutable.

        Example:
            context = step.run(context)
        """
        for day in context.trade_dates:
            paths = {
                "features": self._pm.feature_object(feature_set="previous_day_daily_l2", version="v1", trade_date=day),
                "eligibility": self._pm.processed_object(dataset_name="previous_day_eligibility", version="v1", trade_date=day),
                "rank": self._pm.label_object(label_set="next_day_high_return_rank", version="v1", trade_date=day),
                "event": self._pm.label_object(label_set="next_day_high_top_decile", version="v1", trade_date=day),
            }
            existing = {name: meta.find(pm=self._pm, meta_path=p.meta_path, expected_payload_path=p.payload_path)
                        for name, p in paths.items()}
            if all(existing.values()):
                continue
            previous, _ = self._access.recent_trade_dates(end_date=day, sessions=2)
            following = self._access.next_trade_date(trade_date=day)
            inputs: list[dict[str, str]] = []

            def read(paths: ObjectPaths, expected_date: str) -> pa.Table:
                record = meta.require(pm=self._pm, meta_path=paths.meta_path, expected_payload_path=paths.payload_path)
                for path in (paths.meta_path, record.payload_path):
                    with path.open("rb") as source:
                        digest = hashlib.file_digest(source, "sha256").hexdigest()
                    inputs.append({"path": str(path.relative_to(self._pm.storage_root)), "sha256": digest})
                with pq.ParquetFile(record.payload_path) as source:
                    table = source.read()
                if table.num_rows and pc.all(pc.equal(table.column("trade_date"), expected_date)).as_py() is not True:
                    raise ValueError(f"input partition date mismatch: {paths.payload_path}")
                return table

            l2 = read(self._pm.feature_object(feature_set="l2_stock_1430", version="v1", trade_date=previous), previous).to_pandas()
            daily = read(self._pm.feature_object(feature_set="tushare_daily_basic", version="v1", trade_date=previous), previous).to_pandas()
            statuses = {}
            for dataset in ("stock_basic", "stock_st", "suspend_d"):
                frame = read(self._pm.processed_object(dataset_name=dataset, version="v1", trade_date=previous), previous).to_pandas()
                statuses[dataset] = frame
            features = build_previous_day_features(l2=l2, daily=daily, trade_date=day, previous_trade_date=previous)
            eligibility = qualify_previous_day(features.symbol.tolist(), listed=statuses["stock_basic"],
                st=statuses["stock_st"], suspension=statuses["suspend_d"], target_date=day)
            eligibility["trade_date"] = day
            eligibility["source_date"] = previous
            eligibility["assumed_available_ts"] = features.feature_ready_ts.to_numpy()
            eligibility["decision_ts_utc"] = features.decision_ts_utc.to_numpy()
            qualified = features.loc[eligibility.eligible.to_numpy()].reset_index(drop=True)
            feature_inputs = tuple(inputs)
            # The label ranks on T's original universe before alignment to the P universe.
            target_l2 = read(self._pm.feature_object(feature_set="l2_stock_1430", version="v1", trade_date=day), day)
            prices = []
            factors = []
            for price_day in (day, following):
                prices.append(pa.concat_tables([
                    read(self._pm.processed_object(dataset_name=name, version="v1", trade_date=price_day), price_day)
                    for name in ("sh_stock_trade_1m", "sz_stock_trade_1m")
                ]))
                factors.append(read(self._pm.processed_object(dataset_name="adj_factor", version="v1", trade_date=price_day), price_day).to_pandas())
            ranks = build_next_day_high_labels(symbols=target_l2.column("symbol").to_pylist(),
                entry_minutes=prices[0], exit_minutes=prices[1], entry_factors=factors[0], exit_factors=factors[1],
                trade_date=day, next_trade_date=following).set_index("symbol").reindex(qualified.symbol)
            labels = qualified[["symbol", "trade_date", "decision_ts_utc"]].copy()
            labels["y_rank_return"] = ranks.y_rank_return.to_numpy()
            labels["y_event"] = ranks.y_event.to_numpy()
            labels["label_ready_ts"] = DateTimeUtils.local_time_to_utc_epoch_us(PREOPEN_AVAILABLE_TIME, date.fromisoformat(following))
            tables = {"features": qualified, "eligibility": eligibility,
                      "rank": labels.drop(columns="y_event"), "event": labels.drop(columns="y_rank_return")}
            for name, frame in tables.items():
                if existing[name] is not None:
                    continue
                table = pa.Table.from_pandas(frame, preserve_index=False)
                provenance = {"inputs": feature_inputs if name in {"features", "eligibility"} else inputs,
                              "availability_basis": "previous_session_22_assumption", "historical_received_at": None}
                table = table.replace_schema_metadata({**(table.schema.metadata or {}), b"source_inputs": json.dumps(provenance).encode()})
                _publish_parquet_object(pm=self._pm, paths=paths[name], table=table)
            logs.info(f"✅ 盘前数据; trade_date={day} qualified={len(qualified)} original={len(features)}")
        return context
