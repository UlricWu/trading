# filepath: tests/utils/test_path.py
"""Tests for the formal PathManager storage boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.path import PathManager


def test_constructor_creates_only_the_six_fixed_namespaces(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()

    PathManager(storage_root)

    assert {path.name for path in storage_root.iterdir()} == {
        "raw",
        "staging",
        "processed",
        "features",
        "labels",
        "experiments",
    }
    assert all(path.is_dir() for path in storage_root.iterdir())


def test_constructor_resolves_a_symlinked_storage_root(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    storage_link = tmp_path / "storage-link"
    storage_link.symlink_to(storage_root, target_is_directory=True)

    pm = PathManager(storage_link)

    assert pm.storage_root == storage_root.resolve()
    assert pm.raw_meta(
        broker="tushare",
        source_name="stock_daily",
        trade_date="2026-05-01",
    ) == (
        storage_root.resolve()
        / "raw"
        / "tushare"
        / "stock_daily"
        / "trade_date=2026-05-01"
        / "meta.json"
    )


def test_constructor_rejects_an_invalid_storage_root(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="storage_root must be a pathlib.Path"):
        PathManager(str(tmp_path))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="storage_root must be an absolute path"):
        PathManager(Path("relative/storage"))

    missing_root = tmp_path / "missing"
    with pytest.raises(
        FileNotFoundError,
        match=f"storage_root does not exist: {missing_root}",
    ):
        PathManager(missing_root)

    file_root = tmp_path / "not-a-directory"
    file_root.write_text("content", encoding="utf-8")
    with pytest.raises(
        NotADirectoryError,
        match=f"storage_root is not a directory: {file_root}",
    ):
        PathManager(file_root)


def test_constructor_rejects_a_fixed_namespace_file_conflict(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    conflicting_path = storage_root / "raw"
    conflicting_path.write_text("content", encoding="utf-8")

    with pytest.raises(
        NotADirectoryError,
        match=f"path exists but is not a directory: {conflicting_path}",
    ):
        PathManager(storage_root)


def test_raw_and_staging_paths_preserve_source_native_identity(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)
    raw_partition = (
        storage_root
        / "raw"
        / "level2_ftp"
        / "sh_stock_ordertrade"
        / "trade_date=2026-05-01"
    )
    staging_partition = (
        storage_root
        / "staging"
        / "level2_ftp"
        / "sh_stock_ordertrade"
        / "trade_date=2026-05-01"
    )

    assert (
        pm.raw_payload(
            broker="level2_ftp",
            source_name="sh_stock_ordertrade",
            trade_date="2026-05-01",
            payload_file="SH_Stock_OrderTrade.csv.7z",
        )
        == raw_partition / "SH_Stock_OrderTrade.csv.7z"
    )
    assert (
        pm.raw_meta(
            broker="level2_ftp",
            source_name="sh_stock_ordertrade",
            trade_date="2026-05-01",
        )
        == raw_partition / "meta.json"
    )
    assert (
        pm.staging_payload(
            broker="level2_ftp",
            source_name="sh_stock_ordertrade",
            trade_date="2026-05-01",
            payload_file="SH_Stock_OrderTrade.csv.7z",
        )
        == staging_partition / "SH_Stock_OrderTrade.csv.7z"
    )
    assert not raw_partition.exists()
    assert not staging_partition.exists()


def test_trade_calendar_paths_use_annual_partitions(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)
    raw_partition = (
        storage_root
        / "raw"
        / "tushare"
        / "trade_calendar"
        / "year=2026"
    )
    processed_partition = (
        storage_root
        / "processed"
        / "trade_calendar"
        / "v1"
        / "year=2026"
    )

    assert pm.raw_year_payload(
        broker="tushare",
        source_name="trade_calendar",
        calendar_year=2026,
        payload_file="data.parquet",
    ) == raw_partition / "data.parquet"
    assert pm.raw_year_meta(
        broker="tushare",
        source_name="trade_calendar",
        calendar_year=2026,
    ) == raw_partition / "meta.json"
    assert pm.processed_year_data(
        dataset_name="trade_calendar",
        version="v1",
        calendar_year=2026,
    ) == processed_partition / "data.parquet"
    assert pm.processed_year_meta(
        dataset_name="trade_calendar",
        version="v1",
        calendar_year=2026,
    ) == processed_partition / "meta.json"


def test_formal_dataset_paths_use_canonical_partition_files(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)
    trade_date_partition = "trade_date=2026-05-01"
    processed_version_dir = storage_root / "processed" / "sh_order" / "v1"
    processed_partition = processed_version_dir / trade_date_partition
    feature_partition = (
        storage_root / "features" / "l2_microstructure" / "v2" / trade_date_partition
    )
    label_partition = (
        storage_root / "labels" / "return_5d" / "v3" / trade_date_partition
    )

    assert (
        pm.processed_version_dir(dataset_name="sh_order", version="v1")
        == processed_version_dir
    )
    assert (
        pm.processed_data(
            dataset_name="sh_order",
            version="v1",
            trade_date="2026-05-01",
        )
        == processed_partition / "data.parquet"
    )
    assert (
        pm.processed_meta(
            dataset_name="sh_order",
            version="v1",
            trade_date="2026-05-01",
        )
        == processed_partition / "meta.json"
    )
    assert (
        pm.feature_data(
            feature_set="l2_microstructure",
            version="v2",
            trade_date="2026-05-01",
        )
        == feature_partition / "data.parquet"
    )
    assert (
        pm.feature_meta(
            feature_set="l2_microstructure",
            version="v2",
            trade_date="2026-05-01",
        )
        == feature_partition / "meta.json"
    )
    assert (
        pm.label_data(
            label_set="return_5d",
            version="v3",
            trade_date="2026-05-01",
        )
        == label_partition / "data.parquet"
    )
    assert (
        pm.label_meta(
            label_set="return_5d",
            version="v3",
            trade_date="2026-05-01",
        )
        == label_partition / "meta.json"
    )
    assert not processed_version_dir.exists()
    assert not feature_partition.exists()
    assert not label_partition.exists()


def test_experiment_paths_are_exact_and_experiment_scoped(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)
    experiment_name = "training_2026-05-01_2026-05-31_run-1"
    experiment_dir = storage_root / "experiments" / experiment_name
    training_dir = experiment_dir / "training"
    backtest_dir = experiment_dir / "backtest"
    report_dir = experiment_dir / "report"

    assert pm.experiment_dir(experiment_name=experiment_name) == experiment_dir
    assert pm.experiment_training_dir(experiment_name=experiment_name) == training_dir
    assert pm.experiment_backtest_dir(experiment_name=experiment_name) == backtest_dir
    assert pm.experiment_report_dir(experiment_name=experiment_name) == report_dir
    assert (
        pm.experiment_training_preprocess(experiment_name=experiment_name)
        == training_dir / "preprocess.pkl"
    )
    assert (
        pm.experiment_training_model(experiment_name=experiment_name)
        == training_dir / "model.pkl"
    )
    assert (
        pm.experiment_training_params(experiment_name=experiment_name)
        == training_dir / "params.json"
    )
    assert (
        pm.experiment_training_metrics(experiment_name=experiment_name)
        == training_dir / "metrics.json"
    )
    assert (
        pm.experiment_backtest_metrics(experiment_name=experiment_name)
        == backtest_dir / "metrics.json"
    )
    assert (
        pm.experiment_training_report(experiment_name=experiment_name)
        == report_dir / "training_report.html"
    )
    assert (
        pm.experiment_backtest_report(experiment_name=experiment_name)
        == report_dir / "backtest_report.html"
    )
    assert not experiment_dir.exists()


@pytest.mark.parametrize(
    "experiment_id",
    ["run-1", "A", "model.v2_3", "a" * 64],
)
def test_experiment_id_accepts_the_public_contract(experiment_id: str) -> None:
    assert PathManager.require_experiment_id(experiment_id) == experiment_id


@pytest.mark.parametrize(
    "experiment_id",
    ["", "-run", " run", "run/1", "a" * 65],
)
def test_experiment_id_rejects_values_outside_the_public_contract(
    experiment_id: str,
) -> None:
    with pytest.raises(ValueError, match="experiment_id must match"):
        PathManager.require_experiment_id(experiment_id)


def test_path_segments_allow_unicode_internal_spaces_and_asterisks(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    assert pm.raw_payload(
        broker="券商 A",
        source_name="盘口*快照",
        trade_date="2026-05-01",
        payload_file="盘口 快照.v1.csv.7z",
    ) == (
        storage_root
        / "raw"
        / "券商 A"
        / "盘口*快照"
        / "trade_date=2026-05-01"
        / "盘口 快照.v1.csv.7z"
    )


@pytest.mark.parametrize(
    "bad_segment",
    ["", " leading", "trailing ", ".", "..", "bad/value", "bad\\value", "a\x00b"],
)
def test_path_segments_reject_unsafe_basenames(
    tmp_path: Path,
    bad_segment: str,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    with pytest.raises(ValueError):
        pm.raw_meta(
            broker=bad_segment,
            source_name="stock_daily",
            trade_date="2026-05-01",
        )


def test_each_path_identity_is_validated_at_the_public_boundary(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    with pytest.raises(ValueError, match="source_name must be a safe basename"):
        pm.raw_meta(
            broker="tushare",
            source_name="../stock_daily",
            trade_date="2026-05-01",
        )
    with pytest.raises(ValueError, match="payload_file must be a safe basename"):
        pm.raw_payload(
            broker="tushare",
            source_name="stock_daily",
            trade_date="2026-05-01",
            payload_file="../daily.csv",
        )
    with pytest.raises(ValueError, match="dataset_name must be a safe basename"):
        pm.processed_version_dir(dataset_name="../sh_order", version="v1")
    with pytest.raises(ValueError, match="version must be a safe basename"):
        pm.processed_version_dir(dataset_name="sh_order", version="../v1")
    with pytest.raises(ValueError, match="feature_set must be a safe basename"):
        pm.feature_data(
            feature_set="../microstructure",
            version="v1",
            trade_date="2026-05-01",
        )
    with pytest.raises(ValueError, match="label_set must be a safe basename"):
        pm.label_data(
            label_set="../return_5d",
            version="v1",
            trade_date="2026-05-01",
        )
    with pytest.raises(ValueError, match="experiment_name must be a safe basename"):
        pm.experiment_dir(experiment_name="../experiment")
    with pytest.raises(TypeError, match="broker must be a str"):
        pm.raw_meta(
            broker=1,  # type: ignore[arg-type]
            source_name="stock_daily",
            trade_date="2026-05-01",
        )


@pytest.mark.parametrize(
    "bad_trade_date",
    ["20260501", "2026-02-30", "2026-5-1", " 2026-05-01 "],
)
def test_trade_dates_must_be_canonical_calendar_dates(
    tmp_path: Path,
    bad_trade_date: str,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    with pytest.raises(ValueError):
        pm.feature_data(
            feature_set="l2_microstructure",
            version="v1",
            trade_date=bad_trade_date,
        )


@pytest.mark.parametrize("bad_year", [True, 0, 10000, "2026"])
def test_calendar_years_must_be_canonical_integers(
    tmp_path: Path,
    bad_year: object,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    pm = PathManager(storage_root)

    with pytest.raises((TypeError, ValueError)):
        pm.processed_year_data(
            dataset_name="trade_calendar",
            version="v1",
            calendar_year=bad_year,  # type: ignore[arg-type]
        )
