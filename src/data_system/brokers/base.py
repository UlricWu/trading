# filepath: src/data_system/brokers/base.py
"""Minimal broker protocol for source-native raw object ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Protocol

from src.config.app_config import AppConfig
from src.utils.path import PathManager


class BrokerAdapter(Protocol):
    """Implement source fetching owned by `docs/data/source_contract.md`.

    Example:
        from src.data_system.brokers.tushare import TushareBroker

        adapter: BrokerAdapter = TushareBroker(app_cfg=app_config)
        payload_path = adapter.fetch(
            source_name="daily_bar",
            raw_object="daily_bar",
            trade_date="2026-07-20",
            pm=path_manager,
        )
    """

    name: ClassVar[str]

    def __init__(self, *, app_cfg: AppConfig) -> None:
        """Initialize the adapter from application configuration.

        Example:
            adapter = TushareBroker(app_cfg=app_config)
        """
        ...

    def fetch(
        self,
        *,
        source_name: str,
        raw_object: str,
        trade_date: str,
        pm: PathManager,
    ) -> Path | None:
        """
        Return the completed raw payload path without committing Meta.

        Implementations return `None` when the official source response confirms
        no data for the requested raw object and trade date.
        Implementations that use staging must resolve staging payload paths
        through `PathManager.staging_payload(...)`.

        Example:
            payload_path = adapter.fetch(
                source_name="daily_bar",
                raw_object="daily_bar",
                trade_date="2026-07-20",
                pm=path_manager,
            )
        """
        ...
