from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdapterMode(StrEnum):
    MOCK = "mock"
    CLI = "cli"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    app_name: str = "CloudStack BlueField Integration API"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8081, ge=1, le=65535)
    log_level: str = "INFO"
    eswitch_adapter_mode: AdapterMode = AdapterMode.MOCK
    eswitchctl_path: Path = Path("/usr/local/bin/eswitchctl")
    integration_api_token: SecretStr | None = None

    @model_validator(mode="after")
    def validate_api_token(self) -> "Settings":
        if self.integration_api_token is not None:
            token = self.integration_api_token.get_secret_value()
            if not token.strip():
                raise ValueError("INTEGRATION_API_TOKEN must not be empty")
            if len(token) < 32:
                raise ValueError("INTEGRATION_API_TOKEN must contain at least 32 characters")
        elif self.eswitch_adapter_mode is AdapterMode.CLI:
            raise ValueError("INTEGRATION_API_TOKEN is required in CLI mode")
        return self

    eswitchctl_timeout_seconds: float = Field(default=10.0, gt=0, le=300)


@lru_cache
def get_settings() -> Settings:
    return Settings()
