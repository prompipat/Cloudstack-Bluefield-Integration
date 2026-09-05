from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Response, status

from integration_api.adapters.base import ESwitchAdapter
from integration_api.api.auth import require_api_token
from integration_api.api.dependencies import get_adapter, get_allocation_service
from integration_api.models.allocation import AllocationConstraints, AllocationRequest
from integration_api.models.requests import (
    AllocatePortRequest,
    AttachPortRequest,
    CreateVSwitchRequest,
)
from integration_api.models.responses import (
    AllocationResult,
    AvailablePort,
    PortAttachmentResult,
    VSwitchResult,
)
from integration_api.services.allocation import (
    AllocationFeatureUnavailableError,
    AllocationService,
    InvalidIdempotencyKeyError,
)

AdapterDependency = Annotated[ESwitchAdapter, Depends(get_adapter)]
AllocationServiceDependency = Annotated[AllocationService | None, Depends(get_allocation_service)]
VSwitchPath = Annotated[int, Path(ge=1, le=65535)]
PortPath = Annotated[int, Path(ge=0, le=65535)]

health_router = APIRouter(prefix="/health", tags=["health"])
api_router = APIRouter(
    prefix="/api/v1",
    tags=["eswitch"],
    dependencies=[Depends(require_api_token)],
)


@health_router.get("/live")
def liveness() -> dict[str, str]:
    return {"status": "live"}


@health_router.get("/ready")
def readiness(adapter: AdapterDependency, response: Response) -> dict[str, str]:
    if not adapter.is_ready():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready"}
    return {"status": "ready"}


@api_router.post(
    "/vswitches",
    response_model=VSwitchResult,
    status_code=status.HTTP_201_CREATED,
)
def create_vswitch(request: CreateVSwitchRequest, adapter: AdapterDependency) -> VSwitchResult:
    adapter.create_vswitch(request.vswitch_id)
    return VSwitchResult(vswitch_id=request.vswitch_id)


@api_router.delete("/vswitches/{vswitch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vswitch(vswitch_id: VSwitchPath, adapter: AdapterDependency) -> Response:
    adapter.delete_vswitch(vswitch_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.get("/ports/available", response_model=list[AvailablePort])
def list_available_ports(adapter: AdapterDependency) -> list[AvailablePort]:
    return adapter.list_available_ports()


@api_router.post(
    "/vswitches/{vswitch_id}/ports",
    response_model=PortAttachmentResult,
)
def attach_port(
    vswitch_id: VSwitchPath,
    request: AttachPortRequest,
    adapter: AdapterDependency,
) -> PortAttachmentResult:
    adapter.attach_port(vswitch_id, request.port_id)
    return PortAttachmentResult(vswitch_id=vswitch_id, port_id=request.port_id)


@api_router.post(
    "/vswitches/{vswitch_id}/ports/allocate",
    response_model=AllocationResult,
    status_code=status.HTTP_201_CREATED,
)
def allocate_port(
    vswitch_id: VSwitchPath,
    request: AllocatePortRequest,
    response: Response,
    service: AllocationServiceDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AllocationResult:
    if service is None:
        raise AllocationFeatureUnavailableError(
            "atomic allocation is available only with the mock adapter in Phase 6.4A"
        )
    if idempotency_key is None:
        raise InvalidIdempotencyKeyError("Idempotency-Key is required")
    constraints = request.constraints
    outcome = service.allocate(
        AllocationRequest(
            vswitch_id=vswitch_id,
            expected_host=request.expected_host,
            expected_pf=request.expected_pf,
            constraints=AllocationConstraints(
                excluded_port_ids=()
                if constraints is None
                else tuple(constraints.excluded_port_ids),
                allowed_vf_indices=None
                if constraints is None
                else None
                if constraints.allowed_vf_indices is None
                else tuple(constraints.allowed_vf_indices),
            ),
        ),
        idempotency_key,
    )
    record = outcome.record
    if outcome.replayed:
        response.status_code = status.HTTP_200_OK
    if (
        record.selected_port_id is None
        or record.selected_host is None
        or record.selected_pf is None
        or record.vf_index is None
    ):
        raise RuntimeError("successful allocation record lacks selected identity")
    return AllocationResult(
        allocation_id=record.allocation_id,
        idempotency_key=record.idempotency_key,
        state=record.state,
        vswitch_id=record.request.vswitch_id,
        port_id=record.selected_port_id,
        host=record.selected_host,
        pf=record.selected_pf,
        vf_index=record.vf_index,
    )


@api_router.delete(
    "/vswitches/{vswitch_id}/ports/{port_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def detach_port(
    vswitch_id: VSwitchPath,
    port_id: PortPath,
    adapter: AdapterDependency,
) -> Response:
    adapter.detach_port(vswitch_id, port_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
