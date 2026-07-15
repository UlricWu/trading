# filepath: src/utils/path.py
"""Resolve paths defined by ``docs/data/storage_layout.md``."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq

from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem


class PathManager:
    """Resolve paths below one explicit absolute storage root.

    Construction creates only the fixed top-level layer directories. Business
    partition directories remain caller-owned and are never created here.
    """

    _FORMAL_SEGMENT_RE: Final[re.Pattern[str]] = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$"
    )
    _INPUT_FILES: Final[frozenset[str]] = frozenset(
        {
            "feature_ref.json",
            "label_ref.json",
            "split.json",
            "model_ref.json",
            "market_data_ref.json",
            "backtest_config.json",
        }
    )
    _TRAINING_FILES: Final[frozenset[str]] = frozenset(
        {
            "preprocess.pkl",
            "model.pkl",
            "params.json",
            "metrics.json",
            "predictions.parquet",
        }
    )
    _BACKTEST_FILES: Final[frozenset[str]] = frozenset(
        {
            "model_ref.json",
            "orders.parquet",
            "trades.parquet",
            "positions.parquet",
            "equity_curve.parquet",
            "metrics.json",
        }
    )
    _REPORT_FILES: Final[frozenset[str]] = frozenset(
        {
            "training_report.html",
            "backtest_report.html",
        }
    )
    _FIXED_ROOTS: Final[tuple[str, ...]] = (
        "raw",
        "staging",
        "processed",
        "features",
        "labels",
        "experiments",
        "registry",
    )

    def __init__(self, storage_root: str | Path) -> None:
        self._storage_root = self._normalize_storage_root(storage_root)
        self._ensure_fixed_roots()

    @property
    def storage_root(self) -> Path:
        """Return the immutable resolved storage root."""
        return self._storage_root

    @classmethod
    def from_env(cls) -> PathManager:
        """Construct `PathManager` from `ZERO_STORAGE_ROOT`."""
        storage_root = os.environ.get("ZERO_STORAGE_ROOT")
        if storage_root is None or not storage_root.strip():
            raise ValueError(
                "ZERO_STORAGE_ROOT must be a non-empty absolute directory path"
            )
        return cls(storage_root)

    @staticmethod
    def _normalize_storage_root(storage_root: str | Path) -> Path:
        if isinstance(storage_root, str):
            if not storage_root.strip():
                raise ValueError(
                    "storage_root must be a non-empty absolute directory path"
                )
            path = Path(storage_root)
        elif isinstance(storage_root, Path):
            path = storage_root
        else:
            raise TypeError("storage_root must be a str or Path")

        if not path.is_absolute():
            raise ValueError("storage_root must be an absolute path")
        if not path.exists():
            raise FileNotFoundError(f"storage_root does not exist: {path}")
        if not path.is_dir():
            raise NotADirectoryError(f"storage_root is not a directory: {path}")
        return path.resolve()

    def _ensure_fixed_roots(self) -> None:
        for name in self._FIXED_ROOTS:
            FileSystem.ensure_dir(self._storage_root / name)

    @classmethod
    def _formal_segment(cls, value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a str")
        if not cls._FORMAL_SEGMENT_RE.fullmatch(value):
            raise ValueError(f"{field_name} must be a safe formal path segment")
        return value

    @classmethod
    def _trade_date_partition(cls, trade_date: str) -> str:
        """
        Build the formal data partition segment after trade_date validation.
        """
        return f"trade_date={DateTimeUtils.require_trade_date(trade_date)}"

    @staticmethod
    def _payload_file(payload_file: str) -> str:
        if not isinstance(payload_file, str):
            raise TypeError("payload_file must be a str")
        if not payload_file or payload_file.strip() != payload_file:
            raise ValueError("payload_file must not be empty or padded with spaces")
        if payload_file in {".", ".."}:
            raise ValueError("payload_file must be a safe basename")
        if Path(payload_file).name != payload_file:
            raise ValueError("payload_file must be a safe basename")
        if "/" in payload_file or "\\" in payload_file:
            raise ValueError("payload_file must be a safe basename")
        return payload_file

    @staticmethod
    def _allowed_name(name: str, field_name: str, allowed: frozenset[str]) -> str:
        if not isinstance(name, str):
            raise TypeError(f"{field_name} must be a str")
        if name not in allowed:
            raise ValueError(f"{field_name} is not allowed: {name}")
        return name

    @staticmethod
    def _read_required_parquet(
        path: Path,
        *,
        object_kind: str,
        identity: str,
        columns: Sequence[str] = (),
    ) -> pa.Table:
        if isinstance(columns, (str, bytes)) or not isinstance(columns, Sequence):
            raise TypeError("columns must be a sequence of str values")
        owned_columns = tuple(columns)
        if any(not isinstance(column, str) for column in owned_columns):
            raise TypeError("columns must contain only str values")
        if len(set(owned_columns)) != len(owned_columns):
            raise ValueError("columns must not contain duplicates")
        if not path.is_file():
            raise FileNotFoundError(
                f"missing {object_kind} partition data; {identity} path={path}"
            )

        with pq.ParquetFile(path) as parquet_file:
            available_columns = set(parquet_file.schema_arrow.names)
            missing_columns = [
                column for column in owned_columns if column not in available_columns
            ]
            if missing_columns:
                raise ValueError(
                    f"missing {object_kind} columns {missing_columns}; "
                    f"{identity} path={path}"
                )
            read_columns = None if not owned_columns else list(owned_columns)
            return parquet_file.read(columns=read_columns)

    def raw_root(self) -> Path:
        """Return the raw layer root."""
        return self.storage_root / "raw"

    def staging_root(self) -> Path:
        """Return the operational staging root."""
        return self.storage_root / "staging"

    def staging_payload(
        self,
        broker: str,
        source_name: str,
        trade_date: str,
        payload_file: str = "data.parquet",
    ) -> Path:
        """Return one operational staging payload path for raw ingest."""
        return (
            self.staging_root()
            / self._formal_segment(broker, "source")
            / self._formal_segment(source_name, "raw_dataset")
            / self._trade_date_partition(trade_date)
            / self._payload_file(payload_file)
        )

    def processed_root(self) -> Path:
        """Return the processed layer root."""
        return self.storage_root / "processed"

    def features_root(self) -> Path:
        """Return the features layer root."""
        return self.storage_root / "features"

    def labels_root(self) -> Path:
        """Return the labels layer root."""
        return self.storage_root / "labels"

    def experiments_root(self) -> Path:
        """Return the experiments artifact root."""
        return self.storage_root / "experiments"

    def registry_root(self) -> Path:
        """Return the released-model registry root."""
        return self.storage_root / "registry"

    def raw_dir(self, broker: str, source_name: str, trade_date: str) -> Path:
        """Return one raw dataset partition directory."""
        return (
            self.raw_root()
            / self._formal_segment(broker, "source")
            / self._formal_segment(source_name, "raw_dataset")
            / self._trade_date_partition(trade_date)
        )

    def raw_payload(
        self,
        broker: str,
        source_name: str,
        trade_date: str,
        payload_file: str = "data.parquet",
    ) -> Path:
        """Return the vendor payload path for one raw partition."""
        return self.raw_dir(broker, source_name, trade_date) / self._payload_file(
            payload_file
        )

    def raw_meta(self, broker: str, source_name: str, trade_date: str) -> Path:
        """Return the canonical raw meta path."""
        return self.raw_dir(broker, source_name, trade_date) / "meta.json"

    def processed_dir(
        self,
        dataset_name: str,
        version: str,
        trade_date: str,
    ) -> Path:
        """Return one processed dataset partition directory."""
        return (
            self.processed_root()
            / self._formal_segment(dataset_name, "logical_dataset")
            / self._formal_segment(version, "version")
            / self._trade_date_partition(trade_date)
        )

    def processed_data(
        self,
        dataset_name: str,
        version: str,
        trade_date: str,
    ) -> Path:
        """Return the canonical processed payload path."""
        return self.processed_dir(dataset_name, version, trade_date) / "data.parquet"

    def processed_meta(
        self,
        dataset_name: str,
        version: str,
        trade_date: str,
    ) -> Path:
        """Return the canonical processed meta path."""
        return self.processed_dir(dataset_name, version, trade_date) / "meta.json"

    def read_processed_table(
        self,
        dataset_name: str,
        version: str,
        trade_date: str,
    ) -> pa.Table:
        """Read the canonical processed payload as an Arrow table."""
        return self._read_required_parquet(
            self.processed_data(dataset_name, version, trade_date),
            object_kind="processed",
            identity=(
                f"dataset={dataset_name} version={version} trade_date={trade_date}"
            ),
        )

    def feature_dir(self, feature_set: str, version: str, trade_date: str) -> Path:
        """Return one feature dataset partition directory."""
        return (
            self.features_root()
            / self._formal_segment(feature_set, "feature_set")
            / self._formal_segment(version, "version")
            / self._trade_date_partition(trade_date)
        )

    def feature_data(self, feature_set: str, version: str, trade_date: str) -> Path:
        """Return the canonical feature payload path."""
        return self.feature_dir(feature_set, version, trade_date) / "data.parquet"

    def feature_meta(self, feature_set: str, version: str, trade_date: str) -> Path:
        """Return the canonical feature meta path."""
        return self.feature_dir(feature_set, version, trade_date) / "meta.json"

    def read_feature_table(
        self,
        feature_set: str,
        version: str,
        trade_date: str,
        columns: Sequence[str] = (),
    ) -> pa.Table:
        """Read the canonical feature payload as an Arrow table."""
        return self._read_required_parquet(
            self.feature_data(feature_set, version, trade_date),
            object_kind="feature",
            identity=(
                f"feature_set={feature_set} version={version} trade_date={trade_date}"
            ),
            columns=columns,
        )

    def label_dir(self, label_set: str, version: str, trade_date: str) -> Path:
        """Return one label dataset partition directory."""
        return (
            self.labels_root()
            / self._formal_segment(label_set, "label_set")
            / self._formal_segment(version, "version")
            / self._trade_date_partition(trade_date)
        )

    def label_data(self, label_set: str, version: str, trade_date: str) -> Path:
        """Return the canonical label payload path."""
        return self.label_dir(label_set, version, trade_date) / "data.parquet"

    def label_meta(self, label_set: str, version: str, trade_date: str) -> Path:
        """Return the canonical label meta path."""
        return self.label_dir(label_set, version, trade_date) / "meta.json"

    def read_label_table(
        self,
        label_set: str,
        version: str,
        trade_date: str,
    ) -> pa.Table:
        """Read the canonical label payload as an Arrow table."""
        return self._read_required_parquet(
            self.label_data(label_set, version, trade_date),
            object_kind="label",
            identity=f"label_set={label_set} version={version} trade_date={trade_date}",
        )

    def experiment_dir(self, experiment_name: str) -> Path:
        """Return one experiment root directory."""
        return self.experiments_root() / self._formal_segment(
            experiment_name, "experiment_name"
        )

    def experiment_run_meta(self, experiment_name: str) -> Path:
        """Return the canonical experiment run metadata path."""
        return self.experiment_dir(experiment_name) / "run_meta.json"

    def experiment_inputs_dir(self, experiment_name: str) -> Path:
        """Return the `inputs/` directory for one experiment."""
        return self.experiment_dir(experiment_name) / "inputs"

    def experiment_training_dir(self, experiment_name: str) -> Path:
        """Return the `training/` directory for one experiment."""
        return self.experiment_dir(experiment_name) / "training"

    def experiment_backtest_dir(self, experiment_name: str) -> Path:
        """Return the `backtest/` directory for one experiment."""
        return self.experiment_dir(experiment_name) / "backtest"

    def experiment_report_dir(self, experiment_name: str) -> Path:
        """Return the `report/` directory for one experiment."""
        return self.experiment_dir(experiment_name) / "report"

    def experiment_input_file(self, experiment_name: str, name: str) -> Path:
        """Return one allowed file under `inputs/`."""
        return self.experiment_inputs_dir(experiment_name) / self._allowed_name(
            name,
            "name",
            self._INPUT_FILES,
        )

    def experiment_training_file(self, experiment_name: str, name: str) -> Path:
        """Return one allowed file under `training/`."""
        return self.experiment_training_dir(experiment_name) / self._allowed_name(
            name,
            "name",
            self._TRAINING_FILES,
        )

    def experiment_backtest_file(self, experiment_name: str, name: str) -> Path:
        """Return one allowed file under `backtest/`."""
        return self.experiment_backtest_dir(experiment_name) / self._allowed_name(
            name,
            "name",
            self._BACKTEST_FILES,
        )

    def experiment_report_file(self, experiment_name: str, name: str) -> Path:
        """Return one allowed file under `report/`."""
        return self.experiment_report_dir(experiment_name) / self._allowed_name(
            name,
            "name",
            self._REPORT_FILES,
        )

    def registry_model_root(self, model_name: str) -> Path:
        """Return the root directory for one released-model namespace."""
        return self.registry_root() / self._formal_segment(model_name, "model_name")

    def registry_model_dir(self, model_name: str, version: str) -> Path:
        """Return one released-model directory."""
        return self.registry_model_root(model_name) / self._formal_segment(
            version, "version"
        )

    def registry_preprocess(self, model_name: str, version: str) -> Path:
        """Return the canonical released preprocess path."""
        return self.registry_model_dir(model_name, version) / "preprocess.pkl"

    def registry_model(self, model_name: str, version: str) -> Path:
        """Return the canonical released model path."""
        return self.registry_model_dir(model_name, version) / "model.pkl"

    def registry_model_info(self, model_name: str, version: str) -> Path:
        """Return the canonical released model metadata path."""
        return self.registry_model_dir(model_name, version) / "model_info.json"

    def registry_source_experiment(self, model_name: str, version: str) -> Path:
        """Return the canonical released model lineage pointer."""
        return self.registry_model_dir(model_name, version) / "source_experiment.json"


__all__ = ["PathManager"]
