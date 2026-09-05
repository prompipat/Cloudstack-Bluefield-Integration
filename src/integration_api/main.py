import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from integration_api.adapters.base import ESwitchAdapter
from integration_api.adapters.factory import create_adapter
from integration_api.adapters.mock import MockESwitchAdapter
from integration_api.adapters.mock_allocation import MockAllocationPortBackend
from integration_api.api.routes.eswitch import api_router, health_router
from integration_api.core.config import AdapterMode, Settings, get_settings
from integration_api.core.exceptions import (
    AdapterError,
    AdapterNotReadyError,
    AdapterOSError,
    AdapterTimeoutError,
    CommandContractError,
    DaemonError,
    ExecutableNotFoundError,
    ExecutablePermissionError,
    InvalidAdapterArgumentError,
    PortNotAttachedError,
    PortNotAvailableError,
    ResponseParseError,
    VSwitchAlreadyExistsError,
    VSwitchNotFoundError,
)
from integration_api.services.allocation import (
    AllocationError,
    AllocationFeatureUnavailableError,
    AllocationService,
    AmbiguousAttachOutcomeError,
    IdempotencyConflictError,
    InMemoryAllocationStore,
    InvalidAllocationRequestError,
    InvalidIdempotencyKeyError,
    NoEligibleRepresentorError,
    ProcessLocalAllocationSynchronization,
    ReconciliationRequiredError,
)

logger = logging.getLogger("integration_api.operations")


def _request_id(value: str | None) -> str:
    if value and len(value) <= 128 and all(character.isprintable() for character in value):
        return value
    return str(uuid.uuid4())


def _error_response(status_code: int, code: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": "The eSwitch operation failed."}},
        headers={"X-Request-ID": request_id},
    )


def create_app(
    *,
    settings: Settings | None = None,
    adapter: ESwitchAdapter | None = None,
    allocation_service: AllocationService | None = None,
) -> FastAPI:
    selected_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        selected_adapter = adapter or create_adapter(selected_settings)
        app.state.adapter = selected_adapter
        app.state.allocation_service = None
        if selected_settings.eswitch_adapter_mode is AdapterMode.MOCK:
            if allocation_service is not None:
                app.state.allocation_service = allocation_service
            elif isinstance(selected_adapter, MockESwitchAdapter):
                app.state.allocation_service = AllocationService(
                    MockAllocationPortBackend(selected_adapter),
                    InMemoryAllocationStore(),
                    ProcessLocalAllocationSynchronization(),
                )
        yield

    expose_docs = selected_settings.eswitch_adapter_mode is AdapterMode.MOCK
    app = FastAPI(
        title=selected_settings.app_name,
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.settings = selected_settings

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        started = time.monotonic()
        result = "unhandled_error"
        try:
            response = await call_next(request)
            result = str(response.status_code)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            duration_ms = (time.monotonic() - started) * 1000
            logger.info(
                "operation=%s request_id=%s result=%s duration_ms=%.3f",
                f"{request.method} {request.url.path}",
                request_id,
                result,
                duration_ms,
            )

    @app.exception_handler(VSwitchNotFoundError)
    @app.exception_handler(PortNotAttachedError)
    async def handle_not_found(request: Request, _error: AdapterError) -> JSONResponse:
        return _error_response(404, "resource_not_found", request.state.request_id)

    @app.exception_handler(VSwitchAlreadyExistsError)
    @app.exception_handler(PortNotAvailableError)
    @app.exception_handler(DaemonError)
    async def handle_conflict(request: Request, _error: AdapterError) -> JSONResponse:
        return _error_response(409, "operation_conflict", request.state.request_id)

    @app.exception_handler(InvalidAdapterArgumentError)
    async def handle_invalid_argument(
        request: Request, _error: InvalidAdapterArgumentError
    ) -> JSONResponse:
        return _error_response(422, "invalid_argument", request.state.request_id)

    async def handle_allocation_error(request: Request, error: AllocationError) -> JSONResponse:
        if isinstance(error, (InvalidAllocationRequestError, InvalidIdempotencyKeyError)):
            status_code = status.HTTP_400_BAD_REQUEST
        elif isinstance(error, (IdempotencyConflictError, NoEligibleRepresentorError)):
            status_code = status.HTTP_409_CONFLICT
        elif isinstance(
            error,
            (
                AmbiguousAttachOutcomeError,
                ReconciliationRequiredError,
                AllocationFeatureUnavailableError,
            ),
        ):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        else:
            status_code = status.HTTP_409_CONFLICT
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": error.code, "message": "The allocation operation failed."}},
            headers={"X-Request-ID": request.state.request_id},
        )

    app.add_exception_handler(AllocationError, handle_allocation_error)  # type: ignore[arg-type]

    unavailable_errors = (
        AdapterNotReadyError,
        AdapterTimeoutError,
        AdapterOSError,
        ExecutableNotFoundError,
        ExecutablePermissionError,
    )

    async def handle_unavailable(request: Request, _error: AdapterError) -> JSONResponse:
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "adapter_unavailable",
            request.state.request_id,
        )

    for exception_type in unavailable_errors:
        app.add_exception_handler(exception_type, handle_unavailable)  # type: ignore[arg-type]

    @app.exception_handler(ResponseParseError)
    @app.exception_handler(CommandContractError)
    async def handle_bad_gateway(request: Request, _error: AdapterError) -> JSONResponse:
        return _error_response(502, "invalid_adapter_response", request.state.request_id)

    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()
