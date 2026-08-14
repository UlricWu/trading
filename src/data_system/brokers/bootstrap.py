# filepath: src/data_system/brokers/bootstrap.py
"""Immutable raw-source broker capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from src.data_system.brokers.base import BrokerAdapter
from src.data_system.brokers.level2 import Level2Broker
from src.data_system.brokers.tushare import TushareBroker

BROKER_ADAPTER_CLASSES: Mapping[str, type[BrokerAdapter]] = MappingProxyType(
    {
        TushareBroker.name: TushareBroker,
        Level2Broker.name: Level2Broker,
    }
)
