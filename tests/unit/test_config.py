from pathlib import Path

import pytest
from pydantic import ValidationError

from integration_api.core.config import AdapterMode, Settings


def test_settings_defaults_to_safe_mock_mode() -> None:
    settings = Settings()

    assert settings.eswitch_adapter_mode is AdapterMode.MOCK
    assert settings.eswitchctl_path == Path("/usr/local/bin/eswitchctl")
    assert settings.eswitchctl_timeout_seconds == 10


def test_settings_loads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESWITCH_ADAPTER_MODE", "cli")
    monkeypatch.setenv("ESWITCHCTL_PATH", "/opt/test/eswitchctl")
    monkeypatch.setenv("ESWITCHCTL_TIMEOUT_SECONDS", "2.5")

    settings = Settings()

    assert settings.eswitch_adapter_mode is AdapterMode.CLI
    assert settings.eswitchctl_path == Path("/opt/test/eswitchctl")
    assert settings.eswitchctl_timeout_seconds == 2.5


@pytest.mark.parametrize("timeout", ["0", "-1", "301"])
def test_settings_rejects_invalid_timeout(monkeypatch: pytest.MonkeyPatch, timeout: str) -> None:
    monkeypatch.setenv("ESWITCHCTL_TIMEOUT_SECONDS", timeout)

    with pytest.raises(ValidationError):
        Settings()
