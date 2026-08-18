# filepath: src/data_system/brokers/base.py
"""Minimal broker protocol for source-native raw object ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from src.config.app_config import AppConfig
from src.utils.path import PathManager


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    source_name: str
    trade_date: str
    broker: str
    raw_object: str
    payload_file: str = "data.parquet"


class BrokerAdapter(Protocol):
    """Implement source fetching owned by `docs/data/source_contract.md`.

    Example:
        fetched = adapter.fetch(record=download_plan, pm=path_manager)
    """

    name: ClassVar[str]

    def __init__(self, *, app_cfg: AppConfig) -> None:
        """Initialize the adapter from application configuration."""
        ...

    def fetch(
        self,
        *,
        record: DownloadPlan,
        pm: PathManager,
    ) -> DownloadPlan | None:
        """
        Materialize one source-native raw payload file for ingest archival.

        Implementations return `None` when the official source response confirms
        no data for the requested raw object and trade date.
        Implementations that use staging must resolve staging payload paths
        through `PathManager.staging_payload(...)`.
        """
        ...
