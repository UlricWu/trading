# filepath: src/config/secret_config.py
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SecretConfig(BaseModel):
    """Hold one validated set of formal FTP and Tushare runtime settings.

    Example:
        secret = SecretConfig(
            ftp_host="ftp.example.com",
            ftp_user="researcher",
            ftp_password="password",
            tushare_token="token",
        )
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ftp_host: str = Field(repr=False)
    ftp_port: int = Field(default=21, ge=1, le=65535, strict=True)
    ftp_user: str = Field(repr=False)
    ftp_password: str = Field(min_length=1, repr=False)

    tushare_token: str = Field(repr=False)
    tushare_gateway: str | None = Field(default=None, repr=False)

    @field_validator("ftp_host", "ftp_user", "tushare_token")
    @classmethod
    def _require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("ftp_port", mode="before")
    @classmethod
    def _default_ftp_port(cls, value: object) -> int:
        if value is None or value == "":
            return 21
        if isinstance(value, bool):
            raise ValueError("must be an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError as exc:
                raise ValueError("must be an integer") from exc
        raise ValueError("must be an integer")

    @field_validator("tushare_gateway", mode="before")
    @classmethod
    def _default_tushare_gateway(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value
