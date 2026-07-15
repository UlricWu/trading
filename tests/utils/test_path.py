# filepath: tests/utils/test_path.py
"""Tests for PathManager formal resolver behavior."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.utils.path import PathManager


def test_constructor_creates_fixed_top_level_roots(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()

    pm = PathManager(storage_root)

    assert pm.raw_root() == storage_root / "raw"
    assert pm.staging_root() == storage_root / "staging"
    assert pm.processed_root() == storage_root / "processed"
    assert pm.features_root() == storage_root / "features"
    assert pm.labels_root() == storage_root / "labels"
    assert pm.experiments_root() == storage_root / "experiments"
    assert pm.registry_root() == storage_root / "registry"

    for root in (
        pm.raw_root(),
        pm.staging_root(),
        pm.processed_root(),
        pm.features_root(),
        pm.labels_root(),
        pm.experiments_root(),
        pm.registry_root(),
    ):
        assert root.exists()
        assert root.is_dir()

    assert not pm.processed_dir("sh_order", "v1", "2026-05-01").exists()


def test_from_env_reads_zero_storage_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setenv("ZERO_STORAGE_ROOT", str(storage_root))

    pm = PathManager.from_env()

    assert pm.storage_root == storage_root.resolve()

    attribute_name = "storage_root"
    with pytest.raises(AttributeError):
        setattr(pm, attribute_name, tmp_path)


@pytest.mark.parametrize(
    ("storage_root", "error_type"),
    [
        ("", ValueError),
        ("   ", ValueError),
        ("relative/path", ValueError),
    ],
)
def test_constructor_rejects_bad_storage_root_values(
    storage_root: str,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        PathManager(storage_root)


def test_constructor_rejects_missing_or_non_directory_storage_root(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        PathManager(missing)

    file_root = tmp_path / "not-a-dir"
    file_root.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        PathManager(file_root)


def test_from_env_rejects_missing_or_empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZERO_STORAGE_ROOT", raising=False)
    with pytest.raises(ValueError):
        PathManager.from_env()

    monkeypatch.setenv("ZERO_STORAGE_ROOT", " ")
    with pytest.raises(ValueError):
        PathManager.from_env()


def test_legacy_path_api_is_absent() -> None:
    removed_names = [
        "meta_dir",
        "fact_dir",
        "l2_normalized_dir",
        "external_dir",
        "external_fact_dir",
        "training_run_dir",
        "training_run_file",
        "training_run_model_file",
        "training_run_preprocess_file",
        "training_run_metadata_file",
        "published_models_root",
        "published_model_dir",
        "published_model_versions_dir",
        "published_model_version_dir",
        "published_model_file",
        "published_model_model_file",
        "published_model_preprocess_file",
        "published_model_metadata_file",
        "published_model_latest_link",
        "backtest_root",
        "backtest_run_dir",
        "backtest_report_file",
        "backtest_tables_dir",
        "backtest_table_file",
        "live_root",
        "live_session_dir",
        "live_report_file",
        "live_tables_dir",
        "live_table_file",
        "live_state_root",
        "live_state_file",
        "train_run_dir",
        "backtest_dir",
        "models_dir",
        "model_lineage_dir",
        "model_version_dir",
        "model_latest_dir",
        "shared_dir",
        "cache_dir",
        "pretrained_dir",
        "str_symbol",
    ]

    for name in removed_names:
        assert not hasattr(PathManager, name), f"legacy API should be removed: {name}"


def test_data_resolvers_follow_owner_docs(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    assert pm.raw_dir("tushare", "stock_daily", "2026-05-01") == (
        storage_root / "raw" / "tushare" / "stock_daily" / "trade_date=2026-05-01"
    )
    assert pm.raw_payload(
        "level2_ftp",
        "sh_stock_ordertrade",
        "2026-05-01",
        "SH_Stock_OrderTrade.csv.7z",
    ) == (
        storage_root
        / "raw"
        / "level2_ftp"
        / "sh_stock_ordertrade"
        / "trade_date=2026-05-01"
        / "SH_Stock_OrderTrade.csv.7z"
    )
    assert pm.raw_meta("tushare", "stock_daily", "2026-05-01").name == "meta.json"

    assert pm.staging_payload(
        "level2_ftp",
        "sh_stock_ordertrade",
        "2026-05-01",
        "SH_Stock_OrderTrade.csv.7z",
    ) == (
        storage_root
        / "staging"
        / "level2_ftp"
        / "sh_stock_ordertrade"
        / "trade_date=2026-05-01"
        / "SH_Stock_OrderTrade.csv.7z"
    )

    assert pm.processed_data("sh_order", "v1", "2026-05-01") == (
        storage_root
        / "processed"
        / "sh_order"
        / "v1"
        / "trade_date=2026-05-01"
        / "data.parquet"
    )
    assert pm.processed_meta("sh_order", "v1", "2026-05-01").name == "meta.json"

    assert pm.feature_data("l2_microstructure", "v1", "2026-05-01") == (
        storage_root
        / "features"
        / "l2_microstructure"
        / "v1"
        / "trade_date=2026-05-01"
        / "data.parquet"
    )
    assert pm.feature_meta("l2_microstructure", "v1", "2026-05-01").name == "meta.json"

    assert pm.label_data("return_5d", "v1", "2026-05-01") == (
        storage_root
        / "labels"
        / "return_5d"
        / "v1"
        / "trade_date=2026-05-01"
        / "data.parquet"
    )
    assert pm.label_meta("return_5d", "v1", "2026-05-01").name == "meta.json"


def test_data_table_readers_load_required_parquet_payloads(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)
    table = pa.table({"symbol": ["000001"], "value": [1.0]})

    processed_path = pm.processed_data("daily_bar", "v1", "2026-05-01")
    feature_path = pm.feature_data("daily_features", "v1", "2026-05-01")
    label_path = pm.label_data("daily_label", "v1", "2026-05-01")
    for path in (processed_path, feature_path, label_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)

    assert pm.read_processed_table("daily_bar", "v1", "2026-05-01").to_pydict() == {
        "symbol": ["000001"],
        "value": [1.0],
    }
    assert pm.read_feature_table("daily_features", "v1", "2026-05-01").to_pydict() == {
        "symbol": ["000001"],
        "value": [1.0],
    }
    assert pm.read_label_table("daily_label", "v1", "2026-05-01").to_pydict() == {
        "symbol": ["000001"],
        "value": [1.0],
    }


def test_read_feature_table_selects_columns_and_validates_schema(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)
    table = pa.table(
        {
            "symbol": ["000001"],
            "f_alpha": [1.0],
            "f_beta": [2.0],
        }
    )
    feature_path = pm.feature_data("daily_features", "v1", "2026-05-01")
    feature_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, feature_path)

    selected = pm.read_feature_table(
        "daily_features",
        "v1",
        "2026-05-01",
        columns=["symbol", "f_alpha"],
    )

    assert selected.column_names == ["symbol", "f_alpha"]
    assert selected.to_pydict() == {
        "symbol": ["000001"],
        "f_alpha": [1.0],
    }
    assert pm.read_feature_table(
        "daily_features",
        "v1",
        "2026-05-01",
        columns=(),
    ).column_names == ["symbol", "f_alpha", "f_beta"]

    with pytest.raises(ValueError, match=r"missing feature columns.*f_missing"):
        pm.read_feature_table(
            "daily_features",
            "v1",
            "2026-05-01",
            columns=["symbol", "f_missing"],
        )

    assert pm.read_feature_table(
        "daily_features",
        "v1",
        "2026-05-01",
        columns=("symbol",),
    ).column_names == ["symbol"]

    with pytest.raises(TypeError, match="columns must be a sequence"):
        # Deliberately violate the static contract to verify boundary validation.
        pm.read_feature_table(
            "daily_features",
            "v1",
            "2026-05-01",
            columns=object(),  # type: ignore[arg-type]
        )


def test_data_table_readers_fail_fast_when_payload_is_missing(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    with pytest.raises(FileNotFoundError, match="missing processed partition data"):
        pm.read_processed_table("daily_bar", "v1", "2026-05-01")

    with pytest.raises(FileNotFoundError, match="missing feature partition data"):
        pm.read_feature_table("daily_features", "v1", "2026-05-01")

    with pytest.raises(FileNotFoundError, match="missing label partition data"):
        pm.read_label_table("daily_label", "v1", "2026-05-01")


def test_artifact_resolvers_follow_owner_docs(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    assert pm.experiment_dir("alpha_exp") == storage_root / "experiments" / "alpha_exp"
    assert pm.experiment_run_meta("alpha_exp") == (
        storage_root / "experiments" / "alpha_exp" / "run_meta.json"
    )
    assert pm.experiment_input_file("alpha_exp", "feature_ref.json") == (
        storage_root / "experiments" / "alpha_exp" / "inputs" / "feature_ref.json"
    )
    assert pm.experiment_training_file("alpha_exp", "model.pkl") == (
        storage_root / "experiments" / "alpha_exp" / "training" / "model.pkl"
    )
    assert pm.experiment_backtest_file("alpha_exp", "metrics.json") == (
        storage_root / "experiments" / "alpha_exp" / "backtest" / "metrics.json"
    )
    assert pm.experiment_report_file("alpha_exp", "backtest_report.html") == (
        storage_root / "experiments" / "alpha_exp" / "report" / "backtest_report.html"
    )

    assert (
        pm.registry_model_root("alpha_model")
        == storage_root / "registry" / "alpha_model"
    )
    assert pm.registry_model_dir("alpha_model", "2026-05-01") == (
        storage_root / "registry" / "alpha_model" / "2026-05-01"
    )
    assert pm.registry_preprocess("alpha_model", "2026-05-01").name == "preprocess.pkl"
    assert pm.registry_model("alpha_model", "2026-05-01").name == "model.pkl"
    assert pm.registry_model_info("alpha_model", "2026-05-01").name == "model_info.json"
    assert (
        pm.registry_source_experiment("alpha_model", "2026-05-01").name
        == "source_experiment.json"
    )


def test_artifact_resolvers_reject_unauthorized_file_names(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    with pytest.raises(ValueError):
        pm.experiment_input_file("alpha_exp", "anything.json")

    with pytest.raises(ValueError):
        pm.experiment_training_file("alpha_exp", "model.joblib")


@pytest.mark.parametrize(
    "bad_segment",
    [
        "",
        " leading",
        "two words",
        "bad/value",
        ".",
        "..",
        "/absolute",
        "中文",
        "a" * 256,
    ],
)
def test_formal_segments_are_strictly_validated(
    tmp_path: Path,
    bad_segment: str,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    with pytest.raises((TypeError, ValueError)):
        pm.raw_dir(bad_segment, "stock_daily", "2026-05-01")


@pytest.mark.parametrize(
    "bad_trade_date", ["20260501", "2026-02-30", "2026-5-1", " 2026-05-01 "]
)
def test_trade_date_rejects_invalid_values(tmp_path: Path, bad_trade_date: str) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    with pytest.raises(ValueError):
        pm.feature_dir("l2_microstructure", "v1", bad_trade_date)

    with pytest.raises(TypeError):
        pm.feature_dir("l2_microstructure", "v1", 20260501)  # type: ignore[arg-type]


def test_payload_file_validation_allows_vendor_names_but_rejects_paths(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    allowed = pm.raw_payload(
        "level2_ftp",
        "sz_order",
        "2026-05-01",
        "盘口 快照.v1.csv.7z",
    )
    assert allowed.name == "盘口 快照.v1.csv.7z"

    for bad_payload in ("../x.csv", "nested/x.csv", "/tmp/x.csv", " x.csv "):
        with pytest.raises(ValueError):
            pm.raw_payload("level2_ftp", "sz_order", "2026-05-01", bad_payload)
        with pytest.raises(ValueError):
            pm.staging_payload("level2_ftp", "sz_order", "2026-05-01", bad_payload)
