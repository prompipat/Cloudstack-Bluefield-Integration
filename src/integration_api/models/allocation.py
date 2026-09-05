"""Framework-independent allocation workflow domain models."""

from dataclasses import dataclass, field, replace
from enum import StrEnum


class AllocationState(StrEnum):
    REQUESTED = "REQUESTED"
    ALLOCATING = "ALLOCATING"
    PORT_ATTACHED = "PORT_ATTACHED"
    VM_ATTACHING = "VM_ATTACHING"
    ACTIVE = "ACTIVE"
    RELEASING = "RELEASING"
    RELEASED = "RELEASED"
    COMPENSATING = "COMPENSATING"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


LEGAL_TRANSITIONS: dict[AllocationState, frozenset[AllocationState]] = {
    AllocationState.REQUESTED: frozenset({AllocationState.ALLOCATING, AllocationState.FAILED}),
    AllocationState.ALLOCATING: frozenset(
        {
            AllocationState.PORT_ATTACHED,
            AllocationState.COMPENSATING,
            AllocationState.FAILED,
            AllocationState.RECONCILIATION_REQUIRED,
        }
    ),
    AllocationState.PORT_ATTACHED: frozenset(
        {
            AllocationState.VM_ATTACHING,
            AllocationState.COMPENSATING,
            AllocationState.RELEASING,
            AllocationState.RECONCILIATION_REQUIRED,
        }
    ),
    AllocationState.VM_ATTACHING: frozenset(
        {
            AllocationState.ACTIVE,
            AllocationState.COMPENSATING,
            AllocationState.RECONCILIATION_REQUIRED,
        }
    ),
    AllocationState.ACTIVE: frozenset(
        {
            AllocationState.RELEASING,
            AllocationState.RECONCILIATION_REQUIRED,
        }
    ),
    AllocationState.RELEASING: frozenset(
        {
            AllocationState.RELEASED,
            AllocationState.RECONCILIATION_REQUIRED,
        }
    ),
    AllocationState.COMPENSATING: frozenset(
        {
            AllocationState.FAILED,
            AllocationState.RELEASED,
            AllocationState.RECONCILIATION_REQUIRED,
        }
    ),
    AllocationState.RECONCILIATION_REQUIRED: frozenset(
        {
            AllocationState.PORT_ATTACHED,
            AllocationState.ACTIVE,
            AllocationState.COMPENSATING,
            AllocationState.FAILED,
            AllocationState.RELEASED,
        }
    ),
    AllocationState.RELEASED: frozenset(),
    AllocationState.FAILED: frozenset(),
}


class IllegalStateTransitionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AllocationConstraints:
    excluded_port_ids: tuple[int, ...] = ()
    allowed_vf_indices: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    vswitch_id: int
    expected_host: int
    expected_pf: int
    constraints: AllocationConstraints = field(default_factory=AllocationConstraints)

    def fingerprint(self) -> tuple[object, ...]:
        return (
            self.vswitch_id,
            self.expected_host,
            self.expected_pf,
            tuple(sorted(set(self.constraints.excluded_port_ids))),
            None
            if self.constraints.allowed_vf_indices is None
            else tuple(sorted(set(self.constraints.allowed_vf_indices))),
        )


@dataclass(frozen=True, slots=True)
class AllocationEffects:
    vswitch_created: bool = False
    port_attached: bool = False
    port_attachment_owned: bool = False
    vf_attached: bool = False


@dataclass(frozen=True, slots=True)
class AllocationCheckpoints:
    vswitch_ready: bool = False
    pci_resolved: bool = False


@dataclass(frozen=True, slots=True)
class AllocationRecord:
    allocation_id: str
    idempotency_key: str
    request: AllocationRequest
    state: AllocationState = AllocationState.REQUESTED
    selected_port_id: int | None = None
    selected_host: int | None = None
    selected_pf: int | None = None
    vf_index: int | None = None
    effects: AllocationEffects = field(default_factory=AllocationEffects)
    checkpoints: AllocationCheckpoints = field(default_factory=AllocationCheckpoints)
    error_code: str | None = None
    reconciliation_reason: str | None = None

    def transition(self, state: AllocationState) -> "AllocationRecord":
        if state not in LEGAL_TRANSITIONS[self.state]:
            raise IllegalStateTransitionError(
                f"illegal allocation transition {self.state} -> {state}"
            )
        return replace(self, state=state)
