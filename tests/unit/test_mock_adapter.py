import pytest

from integration_api.adapters.mock import MockESwitchAdapter
from integration_api.core.exceptions import (
    AdapterNotReadyError,
    InvalidAdapterArgumentError,
    PortNotAttachedError,
    PortNotAvailableError,
    VSwitchAlreadyExistsError,
    VSwitchNotFoundError,
)


def test_mock_adapter_lifecycle_and_port_release() -> None:
    adapter = MockESwitchAdapter()
    assert [port.port_id for port in adapter.list_available_ports()] == [0, 1, 2]

    adapter.create_vswitch(100)
    adapter.attach_port(100, 1)
    assert [port.port_id for port in adapter.list_available_ports()] == [0, 2]

    adapter.detach_port(100, 1)
    assert [port.port_id for port in adapter.list_available_ports()] == [0, 1, 2]

    adapter.attach_port(100, 2)
    adapter.delete_vswitch(100)
    assert [port.port_id for port in adapter.list_available_ports()] == [0, 1, 2]


def test_mock_adapter_prevents_duplicate_vswitch() -> None:
    adapter = MockESwitchAdapter()
    adapter.create_vswitch(1)

    with pytest.raises(VSwitchAlreadyExistsError):
        adapter.create_vswitch(1)


def test_mock_adapter_requires_existing_vswitch() -> None:
    adapter = MockESwitchAdapter()

    with pytest.raises(VSwitchNotFoundError):
        adapter.attach_port(10, 1)
    with pytest.raises(VSwitchNotFoundError):
        adapter.delete_vswitch(10)


def test_mock_adapter_prevents_double_assignment() -> None:
    adapter = MockESwitchAdapter()
    adapter.create_vswitch(1)
    adapter.create_vswitch(2)
    adapter.attach_port(1, 1)

    with pytest.raises(PortNotAvailableError):
        adapter.attach_port(2, 1)


def test_mock_adapter_requires_matching_owner_for_detach() -> None:
    adapter = MockESwitchAdapter()
    adapter.create_vswitch(1)
    adapter.create_vswitch(2)
    adapter.attach_port(1, 1)

    with pytest.raises(PortNotAttachedError):
        adapter.detach_port(2, 1)


def test_mock_adapter_readiness_controls_operations() -> None:
    adapter = MockESwitchAdapter(ready=False)
    assert adapter.is_ready() is False

    with pytest.raises(AdapterNotReadyError):
        adapter.list_available_ports()

    adapter.set_ready(True)
    assert adapter.is_ready() is True
    assert adapter.list_available_ports()


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("create_vswitch", (True,)),
        ("create_vswitch", (0,)),
        ("delete_vswitch", (65536,)),
        ("attach_port", (1, True)),
        ("detach_port", (1, -1)),
    ],
)
def test_mock_adapter_validates_numeric_arguments(operation: str, args: tuple[int, ...]) -> None:
    adapter = MockESwitchAdapter()

    with pytest.raises(InvalidAdapterArgumentError):
        getattr(adapter, operation)(*args)
