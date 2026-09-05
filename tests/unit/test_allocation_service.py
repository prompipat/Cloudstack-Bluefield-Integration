from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest

from integration_api.adapters.mock import MockESwitchAdapter
from integration_api.adapters.mock_allocation import MockAllocationPortBackend
from integration_api.models.allocation import (
    AllocationRecord,
    AllocationRequest,
    AllocationState,
    IllegalStateTransitionError,
)
from integration_api.models.responses import AvailablePort, PortType
from integration_api.services.allocation import (
    AllocationService,
    AmbiguousAttachOutcomeError,
    DefinitiveAttachConflictError,
    IdempotencyConflictError,
    InMemoryAllocationStore,
    MembershipObservation,
    NoEligibleRepresentorError,
    ProcessLocalAllocationSynchronization,
    ReconciliationRequiredError,
)


def representor(port_id: int, host: int, pf: int, vf: int) -> AvailablePort:
    return AvailablePort(port_id=port_id, type=PortType.REPRESENTOR, host=host, pf=pf, vf_index=vf)


def uplink(port_id: int = 0) -> AvailablePort:
    return AvailablePort(port_id=port_id, type=PortType.UPLINK, host=None, pf=None, vf_index=None)


class FakeAllocationBackend:
    def __init__(
        self,
        ports: list[AvailablePort],
        actions: dict[int, str] | None = None,
        observations: dict[int, MembershipObservation] | None = None,
    ) -> None:
        self.ports = ports
        self.actions = actions or {}
        self.observations = observations or {}
        self.attach_calls: list[int] = []
        self.observation_calls: list[int] = []

    def list_available_ports(self) -> list[AvailablePort]:
        return list(self.ports)

    def attach_port(self, vswitch_id: int, port_id: int) -> None:
        self.attach_calls.append(port_id)
        action = self.actions.get(port_id)
        if action == "race":
            raise DefinitiveAttachConflictError("race")
        if action == "ambiguous":
            raise AmbiguousAttachOutcomeError("timeout")

    def observe_port_membership(self, vswitch_id: int, port_id: int) -> MembershipObservation:
        self.observation_calls.append(port_id)
        return self.observations.get(port_id, MembershipObservation.UNKNOWN)


def service(backend: FakeAllocationBackend) -> tuple[AllocationService, InMemoryAllocationStore]:
    store = InMemoryAllocationStore()
    return (
        AllocationService(
            backend,
            store,
            ProcessLocalAllocationSynchronization(),
            allocation_id_factory=lambda: "allocation-test-id",
        ),
        store,
    )


def request(host: int = 1, pf: int = 0) -> AllocationRequest:
    return AllocationRequest(vswitch_id=101, expected_host=host, expected_pf=pf)


def test_deterministic_selection_excludes_uplinks_and_port_zero() -> None:
    backend = FakeAllocationBackend(
        [representor(9, 1, 0, 8), uplink(3), representor(0, 1, 0, 99), representor(2, 1, 0, 1)]
    )
    allocator, _ = service(backend)

    outcome = allocator.allocate(request(), "deterministic-key")

    assert outcome.record.selected_port_id == 2
    assert backend.attach_calls == [2]


def test_filters_expected_host_and_pf() -> None:
    backend = FakeAllocationBackend(
        [representor(1, 2, 0, 0), representor(2, 1, 1, 1), representor(3, 1, 0, 2)]
    )
    allocator, _ = service(backend)
    assert allocator.allocate(request(), "filter-key").record.selected_port_id == 3


def test_no_eligible_port() -> None:
    allocator, store = service(FakeAllocationBackend([uplink(), representor(1, 9, 0, 0)]))
    with pytest.raises(NoEligibleRepresentorError):
        allocator.allocate(request(), "none-key")
    assert store.get("none-key").state is AllocationState.FAILED  # type: ignore[union-attr]


def test_success_and_idempotent_replay() -> None:
    backend = FakeAllocationBackend([representor(5, 1, 0, 4)])
    allocator, _ = service(backend)
    first = allocator.allocate(request(), "same-key")
    replay = allocator.allocate(request(), "same-key")
    assert first.record == replay.record
    assert replay.replayed is True
    assert backend.attach_calls == [5]
    assert first.record.effects.port_attached is True
    assert first.record.effects.port_attachment_owned is True
    assert first.record.effects.vswitch_created is False
    assert first.record.effects.vf_attached is False


def test_idempotency_key_request_mismatch() -> None:
    backend = FakeAllocationBackend([representor(5, 1, 0, 4)])
    allocator, _ = service(backend)
    allocator.allocate(request(), "conflict-key")
    with pytest.raises(IdempotencyConflictError):
        allocator.allocate(request(host=2), "conflict-key")
    assert backend.attach_calls == [5]


def test_definitive_race_retries_next_candidate() -> None:
    backend = FakeAllocationBackend(
        [representor(1, 1, 0, 0), representor(2, 1, 0, 1)], actions={1: "race"}
    )
    allocator, _ = service(backend)
    assert allocator.allocate(request(), "race-key").record.selected_port_id == 2
    assert backend.attach_calls == [1, 2]


def test_ambiguous_outcome_observed_attached_is_reservation() -> None:
    backend = FakeAllocationBackend(
        [representor(1, 1, 0, 0)],
        actions={1: "ambiguous"},
        observations={1: MembershipObservation.ATTACHED},
    )
    allocator, _ = service(backend)
    outcome = allocator.allocate(request(), "observed-key")
    assert outcome.record.state is AllocationState.PORT_ATTACHED
    assert outcome.record.effects.port_attached is True
    assert outcome.record.effects.port_attachment_owned is False
    assert backend.attach_calls == [1]
    assert backend.observation_calls == [1]


def test_ambiguous_outcome_unknown_requires_reconciliation_without_retry() -> None:
    backend = FakeAllocationBackend(
        [representor(1, 1, 0, 0), representor(2, 1, 0, 1)],
        actions={1: "ambiguous"},
        observations={1: MembershipObservation.UNKNOWN},
    )
    allocator, store = service(backend)
    with pytest.raises(ReconciliationRequiredError):
        allocator.allocate(request(), "unknown-key")
    assert backend.attach_calls == [1]
    assert store.get("unknown-key").state is AllocationState.RECONCILIATION_REQUIRED  # type: ignore[union-attr]


def test_ambiguous_outcome_observed_absent_does_not_retry() -> None:
    backend = FakeAllocationBackend(
        [representor(1, 1, 0, 0), representor(2, 1, 0, 1)],
        actions={1: "ambiguous"},
        observations={1: MembershipObservation.NOT_ATTACHED},
    )
    allocator, _ = service(backend)
    with pytest.raises(AmbiguousAttachOutcomeError):
        allocator.allocate(request(), "absent-key")
    assert backend.attach_calls == [1]


def test_illegal_state_transition() -> None:
    record = AllocationRecord(allocation_id="id", idempotency_key="key", request=request())
    with pytest.raises(IllegalStateTransitionError):
        record.transition(AllocationState.ACTIVE)


def test_concurrent_process_local_allocations_do_not_duplicate_port() -> None:
    adapter = MockESwitchAdapter([representor(1, 1, 0, 0), representor(2, 1, 0, 1)])
    adapter.create_vswitch(101)
    allocator = AllocationService(
        MockAllocationPortBackend(adapter),
        InMemoryAllocationStore(),
        ProcessLocalAllocationSynchronization(),
    )
    start = Lock()
    start.acquire()

    def allocate(key: str) -> int:
        with start:
            pass
        result = allocator.allocate(request(), key)
        assert result.record.selected_port_id is not None
        return result.record.selected_port_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(allocate, key) for key in ("concurrent-a", "concurrent-b")]
        start.release()
        selected = [future.result() for future in futures]

    assert sorted(selected) == [1, 2]
    assert adapter.list_available_ports() == []
