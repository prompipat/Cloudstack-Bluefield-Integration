from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from integration_api.adapters.base import ESwitchAdapter
from integration_api.api.dependencies import get_adapter
from integration_api.models.requests import AttachPortRequest, CreateVSwitchRequest
from integration_api.models.responses import (
    AvailablePort,
    PortAttachmentResult,
    VSwitchResult,
)

AdapterDependency = Annotated[ESwitchAdapter, Depends(get_adapter)]
VSwitchPath = Annotated[int, Path(ge=1, le=65535)]
PortPath = Annotated[int, Path(ge=0, le=65535)]

health_router = APIRouter(prefix="/health", tags=["health"])
api_router = APIRouter(prefix="/api/v1", tags=["eswitch"])


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
