import logging
from collections.abc import Generator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from integration_api.adapters.mock import MockESwitchAdapter
from integration_api.api.routes.eswitch import api_router
from integration_api.core.config import AdapterMode, Settings
from integration_api.main import create_app

TEST_TOKEN = "deterministic-test-token-1234567890"
WRONG_TOKEN = "different-deterministic-token-123456"


@pytest.fixture
def adapter() -> MockESwitchAdapter:
    return MockESwitchAdapter()


@pytest.fixture
def app_client(adapter: MockESwitchAdapter) -> Generator[TestClient, None, None]:
    app = create_app(
        settings=Settings(integration_api_token=TEST_TOKEN),
        adapter=adapter,
    )
    with TestClient(app) as client:
        yield client


def authorization(token: str = TEST_TOKEN, *, scheme: str = "Bearer") -> dict[str, str]:
    return {"Authorization": f"{scheme} {token}"}


def test_valid_bearer_token_reaches_every_operational_route(
    app_client: TestClient,
) -> None:
    headers = authorization()

    assert app_client.get("/api/v1/ports/available", headers=headers).status_code == 200
    assert (
        app_client.post(
            "/api/v1/vswitches",
            json={"vswitch_id": 100},
            headers=headers,
        ).status_code
        == 201
    )
    assert (
        app_client.post(
            "/api/v1/vswitches/100/ports",
            json={"port_id": 1},
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        app_client.delete(
            "/api/v1/vswitches/100/ports/1",
            headers=headers,
        ).status_code
        == 204
    )
    assert app_client.delete("/api/v1/vswitches/100", headers=headers).status_code == 204


@pytest.mark.parametrize(
    "header",
    [
        None,
        f"Bearer {WRONG_TOKEN}",
        "not-a-valid-header",
        f"Basic {TEST_TOKEN}",
        "Bearer ",
    ],
    ids=["missing", "wrong-token", "malformed", "basic", "empty-bearer"],
)
def test_invalid_credentials_use_same_generic_401(
    app_client: TestClient, header: str | None
) -> None:
    headers = {} if header is None else {"Authorization": header}

    response = app_client.get("/api/v1/ports/available", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing bearer token"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert TEST_TOKEN not in response.text
    assert WRONG_TOKEN not in response.text


def test_bearer_scheme_is_case_insensitive_but_token_is_case_sensitive(
    app_client: TestClient,
) -> None:
    accepted = app_client.get(
        "/api/v1/ports/available",
        headers=authorization(scheme="bEaReR"),
    )
    rejected = app_client.get(
        "/api/v1/ports/available",
        headers=authorization(TEST_TOKEN.upper()),
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 401


def test_health_endpoints_remain_public(app_client: TestClient) -> None:
    assert app_client.get("/health/live").status_code == 200
    assert app_client.get("/health/ready").status_code == 200


def test_rejected_authentication_does_not_invoke_adapter() -> None:
    adapter = Mock()
    app = create_app(
        settings=Settings(integration_api_token=TEST_TOKEN),
        adapter=adapter,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/ports/available",
            headers=authorization(WRONG_TOKEN),
        )

    assert response.status_code == 401
    adapter.list_available_ports.assert_not_called()


def test_token_is_absent_from_response_and_operation_logs(
    app_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="integration_api.operations")

    response = app_client.get(
        "/api/v1/ports/available",
        headers=authorization(WRONG_TOKEN),
    )

    assert response.status_code == 401
    assert TEST_TOKEN not in response.text
    assert WRONG_TOKEN not in response.text
    assert TEST_TOKEN not in caplog.text
    assert WRONG_TOKEN not in caplog.text
    assert "Authorization" not in caplog.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_production_disables_documentation_paths(path: str) -> None:
    settings = Settings(
        eswitch_adapter_mode=AdapterMode.CLI,
        integration_api_token=TEST_TOKEN,
    )
    app = create_app(settings=settings, adapter=MockESwitchAdapter())

    with TestClient(app) as client:
        assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_mock_mode_exposes_documentation_paths(path: str) -> None:
    app = create_app(
        settings=Settings(integration_api_token=TEST_TOKEN),
        adapter=MockESwitchAdapter(),
    )

    with TestClient(app) as client:
        assert client.get(path).status_code == 200


def test_future_route_on_api_router_inherits_authentication() -> None:
    original_route_count = len(api_router.routes)

    @api_router.get("/future-auth-test")
    def future_route() -> dict[str, str]:
        return {"status": "ok"}

    try:
        app = create_app(
            settings=Settings(integration_api_token=TEST_TOKEN),
            adapter=MockESwitchAdapter(),
        )
        with TestClient(app) as client:
            assert client.get("/api/v1/future-auth-test").status_code == 401
            assert (
                client.get(
                    "/api/v1/future-auth-test",
                    headers=authorization(),
                ).status_code
                == 200
            )
    finally:
        del api_router.routes[original_route_count:]


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"params": {"integration_api_token": TEST_TOKEN}},
        {"json": {"vswitch_id": 1, "integration_api_token": TEST_TOKEN}},
    ],
)
def test_token_is_not_accepted_outside_authorization_header(
    app_client: TestClient, request_kwargs: dict[str, object]
) -> None:
    response = app_client.post("/api/v1/vswitches", **request_kwargs)

    assert response.status_code == 401


def test_missing_token_is_allowed_only_for_mock_startup() -> None:
    settings = Settings(eswitch_adapter_mode=AdapterMode.MOCK)

    assert settings.integration_api_token is None


@pytest.mark.parametrize(
    ("token", "message"),
    [("", "must not be empty"), ("short-token", "at least 32 characters")],
)
def test_empty_or_short_token_is_rejected_in_all_modes(token: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message) as caught:
        Settings(eswitch_adapter_mode=AdapterMode.MOCK, integration_api_token=token)

    if token:
        assert token not in str(caught.value)


def test_missing_cli_token_fails_configuration() -> None:
    with pytest.raises(ValidationError, match="required in CLI mode"):
        Settings(eswitch_adapter_mode=AdapterMode.CLI)


def test_valid_cli_token_is_secret_and_accepted() -> None:
    settings = Settings(
        eswitch_adapter_mode=AdapterMode.CLI,
        integration_api_token=TEST_TOKEN,
    )

    assert settings.integration_api_token is not None
    assert settings.integration_api_token.get_secret_value() == TEST_TOKEN
    assert TEST_TOKEN not in repr(settings)
    assert "**********" in repr(settings)
