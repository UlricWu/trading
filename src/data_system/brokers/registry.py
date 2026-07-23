# filepath: src/data_system/brokers/registry.py
"""Registry for broker protocol implementations used by raw ingest."""

from __future__ import annotations

from src.config.app_config import AppConfig
from src.data_system.brokers.base import BrokerAdapter


class BrokerRegistry:
    """
    Register broker adapter classes and expose the minimal raw object API.

    The registry owns pluggable implementation selection only. Raw object
    identity and fetch semantics are defined by `docs/data.md` and implemented
    by each registered `BrokerAdapter` protocol implementation.
    """

    def __init__(self) -> None:
        """Initialize an unfrozen in-memory broker adapter registry."""
        self._registry: dict[str, type[BrokerAdapter]] = {}
        self._frozen: bool = False

    # -------------------------------------------------
    # Registration phase (import time only)
    # -------------------------------------------------
    def register(self, broker_cls: type[BrokerAdapter]) -> None:
        """Register one broker adapter class before the registry is frozen."""
        if self._frozen:
            raise RuntimeError("BrokerRegistry is frozen")

        name = getattr(broker_cls, "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError("BrokerAdapter must define class attribute 'name'")

        if not callable(getattr(broker_cls, "fetch", None)):
            raise TypeError(f"{broker_cls} must define fetch(...)")

        if name in self._registry:
            raise ValueError(f"Broker '{name}' already registered")

        self._registry[name] = broker_cls

    def freeze(self) -> None:
        """Freeze registration so runtime code can only read known adapters."""
        self._frozen = True

    # -------------------------------------------------
    # Read-only API
    # -------------------------------------------------
    def has(self, name: str) -> bool:
        """Return whether a broker adapter class is registered."""
        return name in self._registry

    def names(self) -> list[str]:
        """Return registered broker names in deterministic order."""
        return sorted(self._registry.keys())

    def create(self, name: str, *, app_cfg: AppConfig) -> BrokerAdapter:
        """Instantiate a registered broker adapter or fail for unknown names."""
        return self._broker_cls(name)(app_cfg=app_cfg)

    def supported_source_names(self, name: str) -> tuple[str, ...]:
        """Return source names supported by one broker's expansion registry."""
        broker_cls = self._broker_cls(name)
        provider = getattr(broker_cls, "supported_source_names", None)
        if not callable(provider):
            raise ValueError(
                f"Broker '{name}' does not support broker source expansion"
            )

        source_names = tuple(provider())
        seen: set[str] = set()
        for source_name in source_names:
            if not isinstance(source_name, str) or not source_name:
                raise ValueError(
                    f"Broker '{name}' returned invalid source name {source_name!r}"
                )
            if source_name in seen:
                raise ValueError(
                    f"Broker '{name}' returned duplicate source name {source_name!r}"
                )
            seen.add(source_name)
        return source_names

    def _broker_cls(self, name: str) -> type[BrokerAdapter]:
        """Return a registered broker class or fail the data pipeline."""
        try:
            return self._registry[name]
        except KeyError as exc:
            raise KeyError(f"Broker '{name}' is not registered") from exc
