from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdapterMode(StrEnum):
    MOCK = "mock"
    CLI = "cli"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_name: str = "CloudStack BlueField Integration API"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8081, ge=1, le=65535)
    log_level: str = "INFO"
    eswitch_adapter_mode: AdapterMode = AdapterMode.MOCK
    eswitchctl_path: Path = Path("/usr/local/bin/eswitchctl")
    eswitchctl_timeout_seconds: float = Field(default=10.0, gt=0, le=300)


@lru_cache
def get_settings() -> Settings:
    return Settings()
