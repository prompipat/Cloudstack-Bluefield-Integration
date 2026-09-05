"""Mock-only executable specification for atomic allocation orchestration."""

import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import RLock
from typing import Protocol

from integration_api.models.allocation import (
    AllocationRecord,
    AllocationRequest,
    AllocationState,
)
from integration_api.models.responses import AvailablePort, PortType

MAX_IDEMPOTENCY_KEY_LENGTH = 128


class AllocationError(Exception):
    code = "allocation_failed"


class InvalidAllocationRequestError(AllocationError):
    code = "invalid_allocation_request"


class InvalidIdempotencyKeyError(AllocationError):
    code = "invalid_idempotency_key"


class IdempotencyConflictError(AllocationError):
    code = "idempotency_conflict"


class NoEligibleRepresentorError(AllocationError):
    code = "no_available_representor"


class DefinitiveAttachConflictError(AllocationError):
    code = "attach_conflict"


class AmbiguousAttachOutcomeError(AllocationError):
    code = "ambiguous_attach_outcome"


class ReconciliationRequiredError(AllocationError):
    code = "reconciliation_required"


class AllocationFeatureUnavailableError(AllocationError):
    code = "allocation_mock_only"


class MembershipObservation(StrEnum):
    ATTACHED = "attached"
    NOT_ATTACHED = "not_attached"
    UNKNOWN = "unknown"


class AllocationPortBackend(Protocol):
    def list_available_ports(self) -> list[AvailablePort]: ...
    def attach_port(self, vswitch_id: int, port_id: int) -> None: ...
    def observe_port_membership(self, vswitch_id: int, port_id: int) -> MembershipObservation: ...


class AllocationStore(Protocol):
    def get(self, idempotency_key: str) -> AllocationRecord | None: ...
    def create(self, record: AllocationRecord) -> None: ...
    def save(self, record: AllocationRecord) -> None: ...


class AllocationSynchronization(Protocol):
    def hold(self) -> AbstractContextManager[None]: ...


class InMemoryAllocationStore:
    """Development-only storage; unsafe across processes, restarts, or replicas."""

    def __init__(self) -> None:
        self._records: dict[str, AllocationRecord] = {}

    def get(self, idempotency_key: str) -> AllocationRecord | None:
        return self._records.get(idempotency_key)

    def create(self, record: AllocationRecord) -> None:
        if record.idempotency_key in self._records:
            raise IdempotencyConflictError("idempotency key already exists")
        self._records[record.idempotency_key] = record

    def save(self, record: AllocationRecord) -> None:
        if record.idempotency_key not in self._records:
            raise KeyError(record.idempotency_key)
        self._records[record.idempotency_key] = record


class ProcessLocalAllocationSynchronization:
    """Development-only lock; not distributed and provides no restart fencing."""

    def __init__(self) -> None:
        self._lock = RLock()

    @contextmanager
    def hold(self) -> Iterator[None]:
        with self._lock:
            yield


@dataclass(frozen=True, slots=True)
class AllocationOutcome:
    record: AllocationRecord
    replayed: bool


def validate_idempotency_key(value: str) -> str:
    if not value or not value.strip() or len(value) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise InvalidIdempotencyKeyError(
            f"Idempotency-Key must contain 1..{MAX_IDEMPOTENCY_KEY_LENGTH} characters"
        )
    if not all(character.isprintable() for character in value):
        raise InvalidIdempotencyKeyError("Idempotency-Key must contain printable characters")
    return value


def validate_request(request: AllocationRequest) -> None:
    values = (request.vswitch_id, request.expected_host, request.expected_pf)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise InvalidAllocationRequestError("allocation identities must be integers")
    if not 1 <= request.vswitch_id <= 65535:
        raise InvalidAllocationRequestError("vSwitch ID must be in 1..65535")
    if request.expected_host < 0 or request.expected_pf < 0:
        raise InvalidAllocationRequestError("host and PF identities must be non-negative")
    constraint_values = request.constraints.excluded_port_ids + (
        ()
        if request.constraints.allowed_vf_indices is None
        else request.constraints.allowed_vf_indices
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in constraint_values
    ):
        raise InvalidAllocationRequestError("allocation constraints must be non-negative integers")


class AllocationService:
    def __init__(
        self,
        backend: AllocationPortBackend,
        store: AllocationStore,
        synchronization: AllocationSynchronization,
        *,
        allocation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._backend = backend
        self._store = store
        self._synchronization = synchronization
        self._allocation_id_factory = allocation_id_factory or (lambda: str(uuid.uuid4()))

    def allocate(self, request: AllocationRequest, idempotency_key: str) -> AllocationOutcome:
        validate_request(request)
        key = validate_idempotency_key(idempotency_key)
        with self._synchronization.hold():
            existing = self._store.get(key)
            if existing is not None:
                if existing.request.fingerprint() != request.fingerprint():
                    raise IdempotencyConflictError(
                        "idempotency key belongs to a different allocation request"
                    )
                if existing.state is AllocationState.PORT_ATTACHED:
                    return AllocationOutcome(existing, replayed=True)
                if existing.state is AllocationState.RECONCILIATION_REQUIRED:
                    raise ReconciliationRequiredError(
                        existing.reconciliation_reason or "allocation requires reconciliation"
                    )
                raise AllocationError(existing.error_code or "allocation is not replayable")

            record = AllocationRecord(
                allocation_id=self._allocation_id_factory(),
                idempotency_key=key,
                request=request,
            )
            self._store.create(record)
            record = record.transition(AllocationState.ALLOCATING)
            self._store.save(record)
            return self._allocate_candidates(record)

    def _allocate_candidates(self, record: AllocationRecord) -> AllocationOutcome:
        request = record.request
        excluded = set(request.constraints.excluded_port_ids)
        allowed_vfs = (
            None
            if request.constraints.allowed_vf_indices is None
            else set(request.constraints.allowed_vf_indices)
        )
        candidates = sorted(
            (
                port
                for port in self._backend.list_available_ports()
                if port.type is PortType.REPRESENTOR
                and port.port_id != 0
                and port.host == request.expected_host
                and port.pf == request.expected_pf
                and port.port_id not in excluded
                and port.vf_index is not None
                and (allowed_vfs is None or port.vf_index in allowed_vfs)
            ),
            key=lambda port: (port.port_id, port.vf_index),
        )
        if not candidates:
            failed = replace(
                record.transition(AllocationState.FAILED),
                error_code=NoEligibleRepresentorError.code,
            )
            self._store.save(failed)
            raise NoEligibleRepresentorError("no eligible representor is available")

        for candidate in candidates:
            try:
                self._backend.attach_port(request.vswitch_id, candidate.port_id)
            except DefinitiveAttachConflictError:
                continue
            except AmbiguousAttachOutcomeError:
                return self._resolve_ambiguous(record, candidate)
            return self._attached(record, candidate)

        failed = replace(
            record.transition(AllocationState.FAILED),
            error_code=NoEligibleRepresentorError.code,
        )
        self._store.save(failed)
        raise NoEligibleRepresentorError("all eligible representors lost an attach race")

    def _resolve_ambiguous(
        self, record: AllocationRecord, candidate: AvailablePort
    ) -> AllocationOutcome:
        observation = self._backend.observe_port_membership(
            record.request.vswitch_id, candidate.port_id
        )
        if observation is MembershipObservation.ATTACHED:
            return self._attached(record, candidate, ownership_proven=False)
        if observation is MembershipObservation.NOT_ATTACHED:
            failed = replace(
                record.transition(AllocationState.FAILED),
                error_code=AmbiguousAttachOutcomeError.code,
            )
            self._store.save(failed)
            raise AmbiguousAttachOutcomeError(
                "attach was observed absent; mutation was not retried"
            )
        reconciliation = replace(
            record.transition(AllocationState.RECONCILIATION_REQUIRED),
            selected_port_id=candidate.port_id,
            selected_host=candidate.host,
            selected_pf=candidate.pf,
            vf_index=candidate.vf_index,
            reconciliation_reason="attach outcome could not be proven",
        )
        self._store.save(reconciliation)
        raise ReconciliationRequiredError(reconciliation.reconciliation_reason or "unknown")

    def _attached(
        self,
        record: AllocationRecord,
        candidate: AvailablePort,
        *,
        ownership_proven: bool = True,
    ) -> AllocationOutcome:
        attached = replace(
            record.transition(AllocationState.PORT_ATTACHED),
            selected_port_id=candidate.port_id,
            selected_host=candidate.host,
            selected_pf=candidate.pf,
            vf_index=candidate.vf_index,
            effects=replace(
                record.effects,
                port_attached=True,
                port_attachment_owned=ownership_proven,
            ),
        )
        self._store.save(attached)
        return AllocationOutcome(attached, replayed=False)
