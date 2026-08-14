# filepath: tests/config/test_app_config.py
"""Public loading and override tests for application configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import app_config as app_config_module
from src.config.app_config import AppConfig


def _write_application_files(tmp_path: Path) -> Path:
    config_dir = tmp_path / "src" / "config"
    config_dir.mkdir(parents=True)
    module_path = config_dir / "app_config.py"
    module_path.write_text("", encoding="utf-8")
    (tmp_path / ".env.test").write_text(
        "FTP_HOST=ftp.example.com\n"
        "FTP_USER=researcher\n"
        "FTP_PASSWORD=password\n"
        "TUSHARE_TOKEN=token\n",
        encoding="utf-8",
    )
    (config_dir / "base.yml").write_text(
        """
data:
  brokers: {}
  sources: {}
  feature_sets: {}
  label_sets: {}
model:
  model_params: {}
  train_window_days: 30
  preprocessing:
    missing:
      method: constant
      fill_value: 0.0
  dataset:
    feature_set: features
    feature_version: v1
    label_set: labels
    label_version: v1
    feature_columns: [factor]
    label_column: target
    drop_na: true
    adjustment:
      method: raw
      dataset_name: adj_factor
backtest:
  init_cash: 200000
  min_listing_calendar_days: 120
""".lstrip(),
        encoding="utf-8",
    )
    return module_path


def _install_environment(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_config_module,
        "__file__",
        str(_write_application_files(tmp_path)),
    )
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ZERO_STORAGE_ROOT", str(tmp_path))


def test_override_recursively_merges_any_formal_config_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_environment(tmp_path=tmp_path, monkeypatch=monkeypatch)

    config = AppConfig.load(
        override={
            "model": {"train_window_days": 0},
            "backtest": {"init_cash": 123_456},
        }
    )

    assert config.model.train_window_days == 0
    assert config.model.dataset.feature_columns == ["factor"]
    assert config.backtest.init_cash == 123_456
    assert config.backtest.min_listing_calendar_days == 120


def test_override_rejects_non_config_root_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_environment(tmp_path=tmp_path, monkeypatch=monkeypatch)

    with pytest.raises(ValueError, match="data, model, or backtest"):
        AppConfig.load(override={"storage_root": Path("/other")})


def test_formal_base_config_declares_only_level2_file_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("ZERO_STORAGE_ROOT", str(tmp_path))

    config = AppConfig.load()

    assert tuple(config.data.sources) == (
        "sh_stock_ordertrade",
        "sz_order",
        "sz_trade",
    )
    assert all(
        source.broker == "level2_ftp" and source.group == "offline_level2"
        for source in config.data.sources.values()
    )
