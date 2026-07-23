# filepath: src/data_system/brokers/bootstrap.py
"""Composition root for the fixed raw-source broker registry."""

from __future__ import annotations

from src.data_system.brokers.level2 import Level2Broker
from src.data_system.brokers.registry import BrokerRegistry
from src.data_system.brokers.tushare import TushareBroker


def build_broker_registry() -> BrokerRegistry:
    registry = BrokerRegistry()
    registry.register(TushareBroker)
    registry.register(Level2Broker)
    registry.freeze()
    return registry
