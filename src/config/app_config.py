# filepath: src/config/app_config.py
import yaml
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from dotenv import load_dotenv
import os

from .data_config import DataConfig
from .model_config import ModelConfig
from .secret_config import SecretConfig
from .backtest_config import BacktestConfig
from src import logs

_ATOMIC_OVERRIDE_PATHS = frozenset(
    {
        ("backtest", "strategy"),
    }
)


def project_root() -> str:
    """
    返回项目根目录（基于当前文件位置推导）:
    src/config/app_config.py → src/config → src → project_root
    """
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))


def load_env_auto(root: str) -> None:
    """
    根据环境变量 ENV 自动加载不同 .env 文件
    - ENV=dev → .env.dev
    - ENV=prod → .env.prod
    - 默认 ENV=dev
    """
    env = os.getenv("ENV", "dev").lower()

    env_file_map = {
        "dev": ".env.dev",
        "prod": ".env.prod",
        "test": ".env.test",
    }

    if env not in env_file_map:
        raise ValueError(
            f"Unknown ENV={env}, expected one of {list(env_file_map.keys())}"
        )

    env_file = env_file_map[env]
    env_path = os.path.join(root, env_file)

    if not os.path.exists(env_path):
        raise FileNotFoundError(f"Env file not found: {env_path}")

    logs.info(f"[AppConfig] loaded_environment env={env} file={env_file}")
    load_dotenv(env_path)


class EnvConfig(BaseModel):
    name: Literal["dev", "test", "prod"]


class StorageConfig(BaseModel):
    root: Path


class AppConfig(BaseModel):
    """Application config loader boundary: env, storage, secrets, and section schemas."""

    env: EnvConfig
    secret: SecretConfig
    storage: StorageConfig
    data: DataConfig
    model: ModelConfig
    backtest: BacktestConfig

    @classmethod
    def load(
        cls,
        *,
        path: str | None = None,
        override: Mapping[str, object] | None = None,
    ) -> "AppConfig":
        """Load YAML, inject env-owned fields, then apply the optional override."""
        root = project_root()
        env = os.getenv("ENV", "dev").lower()

        load_env_auto(root)

        # 1) Load YAML base
        if path is None:
            path = os.path.join(root, "src/config/base.yml")

        with Path(path).open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file)
        if not isinstance(raw, dict):
            raise ValueError("application config must be a YAML object")

        raw["env"] = {"name": env}
        raw["storage"] = {"root": Path(os.environ["ZERO_STORAGE_ROOT"])}

        # 2) Inject secret from env
        raw["secret"] = {
            "ftp_host": _env_str("FTP_HOST"),
            "ftp_port": _env_int("FTP_PORT"),
            "ftp_user": _env_str("FTP_USER"),
            "ftp_password": _env_str("FTP_PASSWORD"),
            "tushare_token": _env_str("TUSHARE_TOKEN"),
            "tushare_gateway": _env_str("TUSHARE_GATEWAY"),
            "ad_host": _env_str("AD_HOST"),
            "ad_port": _env_int("AD_PORT"),
            "ad_user": _env_str("AD_USERNAME"),
            "ad_password": _env_str("AD_PASSWORD"),
        }

        # 3) Apply override (if any)
        if override:
            raw = _deep_merge(raw, override)

        return cls(**raw)


def _env_str(name: str, default: str = "") -> str:
    val = os.getenv(name)
    return val if val is not None else default


def _env_int(name: str) -> int | None:
    val = os.getenv(name)
    if val is None or val == "":
        return None
    return int(val)


def _deep_merge(
    base: Mapping[str, object],
    override: Mapping[str, object],
    *,
    path: tuple[str, ...] = (),
) -> dict[str, object]:
    """
    Recursively merge override into base (immutable), with atomic path support.
    """
    out = dict(base)
    for k, v in override.items():
        current_path = (*path, k)
        if current_path in _ATOMIC_OVERRIDE_PATHS:
            out[k] = v
            continue
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v, path=current_path)
        else:
            out[k] = v
    return out
