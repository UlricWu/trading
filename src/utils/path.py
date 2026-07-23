# filepath: src/utils/path.py
"""Resolve paths defined by ``docs/data/storage_layout.md``."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Final

from src.utils.datetime_utils import DateTimeUtils
from src.utils.filesystem import FileSystem


class PathManager:
    """Resolve formal storage paths below one explicit absolute root.

    Construction resolves the existing storage root and creates only the six
    fixed top-level namespaces. Dataset partitions and experiment directories
    are created by their writers.
    """

    _FIXED_ROOTS: Final[tuple[str, ...]] = (
        "raw",
        "staging",
        "processed",
        "features",
        "labels",
        "experiments",
    )
    _EXPERIMENT_ID: Final[re.Pattern[str]] = re.compile(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
    )

    def __init__(self, storage_root: Path) -> None:
        """Bind one existing absolute storage root and ensure its namespaces."""
        if not isinstance(storage_root, Path):
            raise TypeError("storage_root must be a pathlib.Path")
        if not storage_root.is_absolute():
            raise ValueError("storage_root must be an absolute path")
        if not storage_root.exists():
            raise FileNotFoundError(f"storage_root does not exist: {storage_root}")

        resolved_root = storage_root.resolve(strict=True)
        if not resolved_root.is_dir():
            raise NotADirectoryError(f"storage_root is not a directory: {storage_root}")

        self._root = resolved_root
        for name in self._FIXED_ROOTS:
            FileSystem.ensure_dir(self._root / name)

    @property
    def storage_root(self) -> Path:
        """Return the resolved formal storage root."""
        return self._root

    def raw_payload(
        self,
        *,
        broker: str,
        source_name: str,
        trade_date: str,
        payload_file: str,
    ) -> Path:
        """Return the source-native payload path for one raw partition."""
        return self._raw_partition(
            broker=broker,
            source_name=source_name,
            trade_date=trade_date,
        ) / self.require_safe_basename(payload_file, "payload_file")

    def raw_meta(
        self,
        *,
        broker: str,
        source_name: str,
        trade_date: str,
    ) -> Path:
        """Return the object-side metadata path for one raw partition."""
        return (
            self._raw_partition(
                broker=broker,
                source_name=source_name,
                trade_date=trade_date,
            )
            / "meta.json"
        )

    def staging_payload(
        self,
        *,
        broker: str,
        source_name: str,
        trade_date: str,
        payload_file: str,
    ) -> Path:
        """Return the operational staging path for one ingest payload."""
        return (
            self._root
            / "staging"
            / self.require_safe_basename(broker, "broker")
            / self.require_safe_basename(source_name, "source_name")
            / self._trade_date_partition(trade_date)
            / self.require_safe_basename(payload_file, "payload_file")
        )

    def processed_version_dir(
        self,
        *,
        dataset_name: str,
        version: str,
    ) -> Path:
        """Return one processed dataset version directory for partition scans."""
        return (
            self._root
            / "processed"
            / self.require_safe_basename(dataset_name, "dataset_name")
            / self.require_safe_basename(version, "version")
        )

    def processed_data(
        self,
        *,
        dataset_name: str,
        version: str,
        trade_date: str,
    ) -> Path:
        """Return the canonical processed payload path."""
        return (
            self._processed_partition(
                dataset_name=dataset_name,
                version=version,
                trade_date=trade_date,
            )
            / "data.parquet"
        )

    def processed_meta(
        self,
        *,
        dataset_name: str,
        version: str,
        trade_date: str,
    ) -> Path:
        """Return the object-side processed metadata path."""
        return (
            self._processed_partition(
                dataset_name=dataset_name,
                version=version,
                trade_date=trade_date,
            )
            / "meta.json"
        )

    def feature_data(
        self,
        *,
        feature_set: str,
        version: str,
        trade_date: str,
    ) -> Path:
        """Return the canonical feature payload path."""
        return (
            self._feature_partition(
                feature_set=feature_set,
                version=version,
                trade_date=trade_date,
            )
            / "data.parquet"
        )

    def feature_meta(
        self,
        *,
        feature_set: str,
        version: str,
        trade_date: str,
    ) -> Path:
        """Return the object-side feature metadata path."""
        return (
            self._feature_partition(
                feature_set=feature_set,
                version=version,
                trade_date=trade_date,
            )
            / "meta.json"
        )

    def label_data(
        self,
        *,
        label_set: str,
        version: str,
        trade_date: str,
    ) -> Path:
        """Return the canonical label payload path."""
        return (
            self._label_partition(
                label_set=label_set,
                version=version,
                trade_date=trade_date,
            )
            / "data.parquet"
        )

    def label_meta(
        self,
        *,
        label_set: str,
        version: str,
        trade_date: str,
    ) -> Path:
        """Return the object-side label metadata path."""
        return (
            self._label_partition(
                label_set=label_set,
                version=version,
                trade_date=trade_date,
            )
            / "meta.json"
        )

    def experiment_dir(self, *, experiment_name: str) -> Path:
        """Return the namespace directory for one experiment run."""
        return (
            self._root
            / "experiments"
            / self.require_safe_basename(
                experiment_name,
                "experiment_name",
            )
        )

    def experiment_training_dir(self, *, experiment_name: str) -> Path:
        """Return the training artifact directory for one experiment."""
        return self.experiment_dir(experiment_name=experiment_name) / "training"

    def experiment_backtest_dir(self, *, experiment_name: str) -> Path:
        """Return the backtest artifact directory for one experiment."""
        return self.experiment_dir(experiment_name=experiment_name) / "backtest"

    def experiment_report_dir(self, *, experiment_name: str) -> Path:
        """Return the report artifact directory for one experiment."""
        return self.experiment_dir(experiment_name=experiment_name) / "report"

    def experiment_training_preprocess(self, *, experiment_name: str) -> Path:
        """Return one training experiment's preprocessing artifact path."""
        return (
            self.experiment_training_dir(experiment_name=experiment_name)
            / "preprocess.pkl"
        )

    def experiment_training_model(self, *, experiment_name: str) -> Path:
        """Return one training experiment's model artifact path."""
        return (
            self.experiment_training_dir(experiment_name=experiment_name) / "model.pkl"
        )

    def experiment_training_params(self, *, experiment_name: str) -> Path:
        """Return one training experiment's parameter artifact path."""
        return (
            self.experiment_training_dir(experiment_name=experiment_name)
            / "params.json"
        )

    def experiment_training_metrics(self, *, experiment_name: str) -> Path:
        """Return one training experiment's metrics artifact path."""
        return (
            self.experiment_training_dir(experiment_name=experiment_name)
            / "metrics.json"
        )

    def experiment_backtest_metrics(self, *, experiment_name: str) -> Path:
        """Return one backtest experiment's metrics artifact path."""
        return (
            self.experiment_backtest_dir(experiment_name=experiment_name)
            / "metrics.json"
        )

    def experiment_training_report(self, *, experiment_name: str) -> Path:
        """Return one training experiment's HTML report path."""
        return (
            self.experiment_report_dir(experiment_name=experiment_name)
            / "training_report.html"
        )

    def experiment_backtest_report(self, *, experiment_name: str) -> Path:
        """Return one backtest experiment's HTML report path."""
        return (
            self.experiment_report_dir(experiment_name=experiment_name)
            / "backtest_report.html"
        )

    def _raw_partition(
        self,
        *,
        broker: str,
        source_name: str,
        trade_date: str,
    ) -> Path:
        return (
            self._root
            / "raw"
            / self.require_safe_basename(broker, "broker")
            / self.require_safe_basename(source_name, "source_name")
            / self._trade_date_partition(trade_date)
        )

    def _processed_partition(
        self,
        *,
        dataset_name: str,
        version: str,
        trade_date: str,
    ) -> Path:
        return self.processed_version_dir(
            dataset_name=dataset_name,
            version=version,
        ) / self._trade_date_partition(trade_date)

    def _feature_partition(
        self,
        *,
        feature_set: str,
        version: str,
        trade_date: str,
    ) -> Path:
        return (
            self._root
            / "features"
            / self.require_safe_basename(feature_set, "feature_set")
            / self.require_safe_basename(version, "version")
            / self._trade_date_partition(trade_date)
        )

    def _label_partition(
        self,
        *,
        label_set: str,
        version: str,
        trade_date: str,
    ) -> Path:
        return (
            self._root
            / "labels"
            / self.require_safe_basename(label_set, "label_set")
            / self.require_safe_basename(version, "version")
            / self._trade_date_partition(trade_date)
        )

    @staticmethod
    def require_safe_basename(value: str, field_name: str) -> str:
        """Return one path-safe basename or reject it."""
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a str")
        if not value or value.strip() != value:
            raise ValueError(f"{field_name} must be a non-empty unpadded basename")
        if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError(f"{field_name} must be a safe basename")
        return value

    @classmethod
    def require_experiment_id(cls, value: str) -> str:
        """Return an experiment identifier matching the public CLI contract."""
        if not isinstance(value, str):
            raise TypeError("experiment_id must be a str")
        if cls._EXPERIMENT_ID.fullmatch(value) is None:
            raise ValueError(
                "experiment_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
            )
        return value

    @staticmethod
    def _trade_date_partition(trade_date: str) -> str:
        validated_trade_date = DateTimeUtils.require_system_date(
            trade_date,
            field_name="trade_date",
        )
        return f"trade_date={validated_trade_date}"
