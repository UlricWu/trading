# filepath: src/config/secret_config.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SecretConfig(BaseModel):
    """Hold one validated set of formal Tushare runtime settings.

    Example:
        secret = SecretConfig(
            tushare_token="token",
        )
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tushare_token: str = Field(repr=False)
    tushare_gateway: str | None = Field(default=None, repr=False)

    @field_validator("tushare_token")
    @classmethod
    def _require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("tushare_gateway", mode="before")
    @classmethod
    def _default_tushare_gateway(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value
