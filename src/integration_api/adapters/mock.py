from collections.abc import Iterable
from threading import RLock

from integration_api.core.exceptions import (
    AdapterNotReadyError,
    InvalidAdapterArgumentError,
    PortNotAttachedError,
    PortNotAvailableError,
    VSwitchAlreadyExistsError,
    VSwitchNotFoundError,
)
from integration_api.models.responses import AvailablePort, PortType


def default_mock_ports() -> tuple[AvailablePort, ...]:
    return (
        AvailablePort(port_id=0, type=PortType.UPLINK, host=None, pf=None, vf_index=None),
        AvailablePort(port_id=1, type=PortType.REPRESENTOR, host=1, pf=0, vf_index=0),
        AvailablePort(port_id=2, type=PortType.REPRESENTOR, host=1, pf=0, vf_index=1),
    )


def _validate_vswitch_id(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise InvalidAdapterArgumentError("vSwitch ID must be an integer from 1 through 65535")


def _validate_port_id(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
        raise InvalidAdapterArgumentError("port ID must be an integer from 0 through 65535")


class MockESwitchAdapter:
    def __init__(self, ports: Iterable[AvailablePort] | None = None, *, ready: bool = True) -> None:
        selected_ports = default_mock_ports() if ports is None else tuple(ports)
        if len({port.port_id for port in selected_ports}) != len(selected_ports):
            raise ValueError("mock port IDs must be unique")
        self._ports = {port.port_id: port for port in selected_ports}
        self._vswitches: dict[int, set[int]] = {}
        self._port_owners: dict[int, int] = {}
        self._ready = ready
        self._lock = RLock()

    def is_ready(self) -> bool:
        return self._ready

    def set_ready(self, ready: bool) -> None:
        self._ready = ready

    def _require_ready(self) -> None:
        if not self._ready:
            raise AdapterNotReadyError("mock adapter is not ready")

    def create_vswitch(self, vswitch_id: int) -> None:
        _validate_vswitch_id(vswitch_id)
        with self._lock:
            self._require_ready()
            if vswitch_id in self._vswitches:
                raise VSwitchAlreadyExistsError(f"vSwitch {vswitch_id} already exists")
            self._vswitches[vswitch_id] = set()

    def delete_vswitch(self, vswitch_id: int) -> None:
        _validate_vswitch_id(vswitch_id)
        with self._lock:
            self._require_ready()
            members = self._vswitches.pop(vswitch_id, None)
            if members is None:
                raise VSwitchNotFoundError(f"vSwitch {vswitch_id} does not exist")
            for port_id in members:
                del self._port_owners[port_id]

    def list_available_ports(self) -> list[AvailablePort]:
        with self._lock:
            self._require_ready()
            return [
                port
                for port_id, port in sorted(self._ports.items())
                if port_id not in self._port_owners
            ]

    def attach_port(self, vswitch_id: int, port_id: int) -> None:
        _validate_vswitch_id(vswitch_id)
        _validate_port_id(port_id)
        with self._lock:
            self._require_ready()
            if vswitch_id not in self._vswitches:
                raise VSwitchNotFoundError(f"vSwitch {vswitch_id} does not exist")
            if port_id not in self._ports or port_id in self._port_owners:
                raise PortNotAvailableError(f"port {port_id} is not available")
            self._vswitches[vswitch_id].add(port_id)
            self._port_owners[port_id] = vswitch_id

    def detach_port(self, vswitch_id: int, port_id: int) -> None:
        _validate_vswitch_id(vswitch_id)
        _validate_port_id(port_id)
        with self._lock:
            self._require_ready()
            if vswitch_id not in self._vswitches:
                raise VSwitchNotFoundError(f"vSwitch {vswitch_id} does not exist")
            if self._port_owners.get(port_id) != vswitch_id:
                raise PortNotAttachedError(
                    f"port {port_id} is not attached to vSwitch {vswitch_id}"
                )
            self._vswitches[vswitch_id].remove(port_id)
            del self._port_owners[port_id]
