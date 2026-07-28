# filepath: src/config/app_config.py
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict

from .backtest_config import BacktestConfig
from .data_config import DataConfig
from .model_config import ModelConfig
from .secret_config import SecretConfig

_ATOMIC_OVERRIDE_PATHS = frozenset(
    {
        ("backtest", "strategy"),
    }
)
_CONFIG_SECTIONS = ("data", "model", "backtest")
_ENVIRONMENTS = ("dev", "test", "prod")


class AppConfig(BaseModel):
    """Provide one validated snapshot of every application configuration section.

    Example:
        config = AppConfig.load()
        model_config = config.model
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: Literal["dev", "test", "prod"]
    storage_root: Path
    secret: SecretConfig
    data: DataConfig
    model: ModelConfig
    backtest: BacktestConfig

    @classmethod
    def load(
        cls,
        *,
        override: Mapping[str, object] | None = None,
    ) -> "AppConfig":
        """Load the formal environment and YAML config, then validate one snapshot.

        Example:
            config = AppConfig.load(
                override={"backtest": {"model": {"name": "training-1"}}}
            )
            backtest_config = config.backtest
        """
        environment = os.getenv("ENV", "dev").lower()
        if environment not in _ENVIRONMENTS:
            raise ValueError(
                f"Unknown ENV={environment}, expected one of {list(_ENVIRONMENTS)}"
            )

        module_path = Path(__file__).resolve()
        env_path = module_path.parents[2] / f".env.{environment}"
        if not env_path.is_file():
            raise FileNotFoundError(f"Env file not found: {env_path}")
        env_values = dotenv_values(env_path)

        with module_path.with_name("base.yml").open("r", encoding="utf-8") as file:
            document = yaml.safe_load(file)
        if not isinstance(document, dict):
            raise ValueError("application config must be a YAML object")

        sections = {
            name: document[name] for name in _CONFIG_SECTIONS if name in document
        }
        if override is not None:
            invalid_roots = [name for name in override if name not in _CONFIG_SECTIONS]
            if invalid_roots:
                raise ValueError(
                    "override may only contain data, model, or backtest; "
                    f"got={invalid_roots!r}"
                )
            sections = _deep_merge(sections, override)

        return cls.model_validate(
            {
                "environment": environment,
                "storage_root": Path(os.environ["ZERO_STORAGE_ROOT"]),
                "secret": {
                    "ftp_host": env_values.get("FTP_HOST"),
                    "ftp_port": env_values.get("FTP_PORT"),
                    "ftp_user": env_values.get("FTP_USER"),
                    "ftp_password": env_values.get("FTP_PASSWORD"),
                    "tushare_token": env_values.get("TUSHARE_TOKEN"),
                    "tushare_gateway": env_values.get("TUSHARE_GATEWAY"),
                },
                **sections,
            }
        )


def _deep_merge(
    base: Mapping[str, object],
    override: Mapping[str, object],
    *,
    path: tuple[str, ...] = (),
) -> dict[str, object]:
    """Recursively merge mappings while replacing configured atomic paths."""
    out = dict(base)
    for key, value in override.items():
        current_path = (*path, key)
        if current_path in _ATOMIC_OVERRIDE_PATHS:
            out[key] = value
            continue
        base_value = out.get(key)
        if isinstance(base_value, Mapping) and isinstance(value, Mapping):
            out[key] = _deep_merge(base_value, value, path=current_path)
        else:
            out[key] = value
    return out
