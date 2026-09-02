from integration_api.adapters.base import ESwitchAdapter
from integration_api.adapters.cli import CliESwitchAdapter
from integration_api.adapters.mock import MockESwitchAdapter
from integration_api.core.config import AdapterMode, Settings


def create_adapter(settings: Settings) -> ESwitchAdapter:
    if settings.eswitch_adapter_mode is AdapterMode.MOCK:
        return MockESwitchAdapter()
    return CliESwitchAdapter(
        executable_path=settings.eswitchctl_path,
        timeout_seconds=settings.eswitchctl_timeout_seconds,
    )
