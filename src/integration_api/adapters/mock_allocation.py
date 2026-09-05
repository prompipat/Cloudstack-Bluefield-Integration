"""Mock-only adapter from existing fake eSwitch behavior to allocation protocols."""

from integration_api.adapters.mock import MockESwitchAdapter
from integration_api.core.exceptions import (
    AdapterOSError,
    AdapterTimeoutError,
    PortNotAvailableError,
)
from integration_api.models.responses import AvailablePort
from integration_api.services.allocation import (
    AmbiguousAttachOutcomeError,
    DefinitiveAttachConflictError,
    MembershipObservation,
)


class MockAllocationPortBackend:
    def __init__(self, adapter: MockESwitchAdapter) -> None:
        self._adapter = adapter

    def list_available_ports(self) -> list[AvailablePort]:
        return self._adapter.list_available_ports()

    def attach_port(self, vswitch_id: int, port_id: int) -> None:
        try:
            self._adapter.attach_port(vswitch_id, port_id)
        except PortNotAvailableError as error:
            raise DefinitiveAttachConflictError("port lost an attach race") from error
        except (AdapterTimeoutError, AdapterOSError) as error:
            raise AmbiguousAttachOutcomeError("attach result is ambiguous") from error

    def observe_port_membership(self, vswitch_id: int, port_id: int) -> MembershipObservation:
        owner = self._adapter.observe_port_owner(port_id)
        if owner == vswitch_id:
            return MembershipObservation.ATTACHED
        if owner is None:
            return MembershipObservation.NOT_ATTACHED
        return MembershipObservation.UNKNOWN
