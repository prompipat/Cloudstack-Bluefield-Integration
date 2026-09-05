from fastapi import Request

from integration_api.adapters.base import ESwitchAdapter
from integration_api.services.allocation import AllocationService


def get_adapter(request: Request) -> ESwitchAdapter:
    return request.app.state.adapter  # type: ignore[no-any-return]


def get_allocation_service(request: Request) -> AllocationService | None:
    return request.app.state.allocation_service  # type: ignore[no-any-return]
