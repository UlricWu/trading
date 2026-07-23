# filepath: src/config/secret_config.py
from __future__ import annotations

from pydantic import BaseModel, Field


class SecretConfig(BaseModel):
    ftp_host: str = Field(default="", repr=False)
    ftp_port: int | None = None
    ftp_user: str = Field(default="", repr=False)
    ftp_password: str = Field(default="", repr=False)

    tushare_token: str = Field(default="", repr=False)
    tushare_gateway: str = Field(default="")
    ad_host: str = Field(default="", repr=False)
    ad_port: int | None = None
    ad_user: str = Field(default="", repr=False)
    ad_password: str = Field(default="", repr=False)
