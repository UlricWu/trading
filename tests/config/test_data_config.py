# filepath: tests/config/test_data_config.py
"""Schema tests for broker and source configuration ownership."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.data_config import BrokerConfig, DataConfig, SourceConfig


def test_broker_config_rejects_the_removed_normalize_profile_selector() -> None:
    with pytest.raises(ValidationError):
        BrokerConfig.model_validate({"normalize_profile": "v1"})


def test_source_config_rejects_the_removed_broker_expansion_selector() -> None:
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(
            {
                "enabled": True,
                "broker": "tushare",
                "group": "offline_standard",
                "raw_object": "daily_bar",
                "outputs": ["daily_bar"],
                "use_broker_sources": True,
            }
        )


def test_data_config_rejects_configured_tushare_sources() -> None:
    with pytest.raises(ValidationError, match="Tushare source selection is code-owned"):
        DataConfig.model_validate(
            {
                "brokers": {"tushare": {}},
                "sources": {
                    "daily_bar": {
                        "enabled": True,
                        "broker": "tushare",
                        "group": "offline_standard",
                        "raw_object": "daily_bar",
                        "outputs": ["daily_bar"],
                    }
                },
            }
        )


def test_data_config_rejects_non_level2_source_groups() -> None:
    with pytest.raises(ValidationError, match="group must be 'offline_level2'"):
        DataConfig.model_validate(
            {
                "brokers": {
                    "level2_ftp": {
                        "baidupcs_go": "BaiduPCS-Go",
                        "remote_path_templates": ["/level2/{date}/{file}"],
                        "raw_cache_days": 5,
                    }
                },
                "sources": {
                    "sz_trade": {
                        "enabled": True,
                        "broker": "level2_ftp",
                        "group": "offline_standard",
                        "raw_object": "SZ_Trade",
                        "outputs": ["sz_trade"],
                    }
                },
            }
        )


def test_data_config_accepts_complete_level2_file_sources() -> None:
    config = DataConfig.model_validate(
        {
            "brokers": {
                "level2_ftp": {
                    "baidupcs_go": "BaiduPCS-Go",
                        "remote_path_templates": ["/level2/{date}/{file}"],
                    "raw_cache_days": 5,
                }
            },
            "sources": {
                "sh_trade": {
                    "enabled": True,
                    "broker": "level2_ftp",
                    "group": "offline_level2",
                    "raw_object": "SH_Stock_OrderTrade",
                    "outputs": ["sh_trade"],
                },
                "sz_order": {
                    "enabled": False,
                    "broker": "level2_ftp",
                    "group": "offline_level2",
                    "raw_object": "SZ_Order",
                    "outputs": [],
                },
            },
        }
    )

    assert tuple(config.sources) == ("sh_trade", "sz_order")


@pytest.mark.parametrize("template", [
    "relative/{date}/{file}", "/level2/{file}", "/level2/{date}/fixed.csv.7z",
    "/level2/{unknown}/{date}/{file}", "/level2/{date}/../{file}",
    "/level2/{date}/{file!r}", "/level2/{date}/{file.name}",
])
def test_baidu_templates_require_unambiguous_daily_archive_paths(template: str) -> None:
    with pytest.raises(ValidationError):
        DataConfig(brokers={"level2_ftp": BrokerConfig(
            baidupcs_go="BaiduPCS-Go", remote_path_templates=(template,), raw_cache_days=5,
        )})


def test_ftp_transport_configuration_is_no_longer_accepted() -> None:
    with pytest.raises(ValidationError):
        BrokerConfig.model_validate({"remote_root": "level2", "ftp_backend": "ftplib"})
