# filepath: tests/access/test_feature_label.py
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.access import meta
from src.access.feature_label import read_features, read_labels
from src.utils.path import PathManager


def test_reader_preserves_complete_keys_row_order_and_requested_feature_order(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    feature_path = pm.feature_object(feature_set="l2_stock_1430", version="v1", trade_date="2026-07-01").payload_path
    feature_path.parent.mkdir(parents=True)
    pd.DataFrame({"symbol": ["600000", "000001"], "trade_date": ["2026-07-01"] * 2,
                  "decision_ts_utc": [100, 100], "a": [1.0, np.nan], "b": [2.0, 0.0]}).to_parquet(feature_path, index=False)
    meta.commit(pm=pm, payload_path=feature_path)
    frame = read_features(pm=pm, feature_set="l2_stock_1430", version="v1", trade_date="2026-07-01", feature_names=("b", "a"))
    assert list(frame) == ["symbol", "trade_date", "decision_ts_utc", "b", "a"]
    assert frame["symbol"].tolist() == ["600000", "000001"]
    assert np.isnan(frame["a"].iloc[1])
    assert frame["b"].iloc[1] == 0.0


def test_unknown_labels_are_retained_for_the_numeric_evaluation_boundary(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    path = pm.label_object(label_set="rank", version="v1", trade_date="2026-07-01").payload_path
    path.parent.mkdir(parents=True)
    frame = pd.DataFrame({"symbol": ["000001", "600000"], "trade_date": ["2026-07-01"] * 2, "target": [1.0, np.nan]})
    frame.to_parquet(path, index=False)
    meta.commit(pm=pm, payload_path=path)
    loaded = read_labels(pm=pm, label_set="rank", version="v1", trade_date="2026-07-01", label_column="target")
    pd.testing.assert_frame_equal(loaded, frame)


@pytest.mark.parametrize("partition", ["feature", "label"])
def test_1430_identity_requires_its_decision_time_key(tmp_path: Path, partition: str) -> None:
    pm = PathManager(tmp_path)
    if partition == "feature":
        path = pm.feature_object(feature_set="l2_stock_1430", version="v1", trade_date="2026-07-01").payload_path
    else:
        path = pm.label_object(label_set="l2_stock_1430_t1_vwap_rank", version="v1", trade_date="2026-07-01").payload_path
    path.parent.mkdir(parents=True)
    pd.DataFrame({"symbol": ["000001"], "trade_date": ["2026-07-01"], "value": [1.0]}).to_parquet(path, index=False)
    meta.commit(pm=pm, payload_path=path)
    with pytest.raises(ValueError, match="decision_ts_utc"):
        if partition == "feature":
            read_features(pm=pm, feature_set="l2_stock_1430", version="v1", trade_date="2026-07-01", feature_names=("value",))
        else:
            read_labels(pm=pm, label_set="l2_stock_1430_t1_vwap_rank", version="v1", trade_date="2026-07-01", label_column="value")


def test_uncommitted_payload_cannot_be_consumed(tmp_path: Path) -> None:
    pm = PathManager(tmp_path)
    path = pm.feature_object(feature_set="daily", version="v1", trade_date="2026-07-01").payload_path
    path.parent.mkdir(parents=True)
    pd.DataFrame({"symbol": ["000001"], "trade_date": ["2026-07-01"], "f": [1.0]}).to_parquet(path, index=False)
    with pytest.raises(FileNotFoundError):
        read_features(pm=pm, feature_set="daily", version="v1", trade_date="2026-07-01", feature_names=("f",))


@pytest.mark.parametrize("invalid", ["duplicate", "wrong_date", "metadata_feature"])
def test_invalid_partition_identity_or_values_fail(tmp_path: Path, invalid: str) -> None:
    pm = PathManager(tmp_path)
    path = pm.feature_object(feature_set="daily", version="v1", trade_date="2026-07-01").payload_path
    path.parent.mkdir(parents=True)
    frame = pd.DataFrame({"symbol": ["000001", "600000"], "trade_date": ["2026-07-01"] * 2, "f": [0.0, 1.0]})
    if invalid == "duplicate":
        frame["symbol"] = ["000001", "000001"]
    elif invalid == "wrong_date":
        frame["trade_date"] = ["2026-07-02"] * 2
    frame.to_parquet(path, index=False)
    meta.commit(pm=pm, payload_path=path)
    with pytest.raises(ValueError):
        read_features(pm=pm, feature_set="daily", version="v1", trade_date="2026-07-01", feature_names=("trade_date",) if invalid == "metadata_feature" else ("f",))
