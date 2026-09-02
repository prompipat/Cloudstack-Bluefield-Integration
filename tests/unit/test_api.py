from collections.abc import Generator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from integration_api.adapters.mock import MockESwitchAdapter
from integration_api.core.config import Settings
from integration_api.core.exceptions import DaemonError
from integration_api.main import create_app


@pytest.fixture
def adapter() -> MockESwitchAdapter:
    return MockESwitchAdapter()


@pytest.fixture
def client(adapter: MockESwitchAdapter) -> Generator[TestClient, None, None]:
    app = create_app(settings=Settings(), adapter=adapter)
    with TestClient(app) as test_client:
        yield test_client


def test_liveness_does_not_call_adapter() -> None:
    adapter = Mock()
    adapter.is_ready.side_effect = AssertionError("liveness called adapter")
    app = create_app(settings=Settings(), adapter=adapter)

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}
    adapter.is_ready.assert_not_called()


def test_readiness_reports_mock_state(client: TestClient, adapter: MockESwitchAdapter) -> None:
    assert client.get("/health/ready").json() == {"status": "ready"}

    adapter.set_ready(False)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_vswitch_and_port_api_lifecycle(client: TestClient) -> None:
    response = client.post("/api/v1/vswitches", json={"vswitch_id": 100})
    assert response.status_code == 201
    assert response.json() == {"vswitch_id": 100}

    available = client.get("/api/v1/ports/available")
    assert available.status_code == 200
    assert available.json()[1] == {
        "port_id": 1,
        "type": "representor",
        "host": 1,
        "pf": 0,
        "vf_index": 0,
    }

    attached = client.post("/api/v1/vswitches/100/ports", json={"port_id": 1})
    assert attached.status_code == 200
    assert attached.json() == {"vswitch_id": 100, "port_id": 1}

    assert client.delete("/api/v1/vswitches/100/ports/1").status_code == 204
    assert client.delete("/api/v1/vswitches/100").status_code == 204


@pytest.mark.parametrize(
    "body",
    [
        {"vswitch_id": True},
        {"vswitch_id": 0},
        {"vswitch_id": 65536},
        {"vswitch_id": 1, "command": "status"},
    ],
)
def test_create_validation_rejects_invalid_body(
    client: TestClient, body: dict[str, object]
) -> None:
    assert client.post("/api/v1/vswitches", json=body).status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("DELETE", "/api/v1/vswitches/0", None),
        ("DELETE", "/api/v1/vswitches/65536", None),
        ("POST", "/api/v1/vswitches/1/ports", {"port_id": True}),
        ("POST", "/api/v1/vswitches/1/ports", {"port_id": 65536}),
        ("DELETE", "/api/v1/vswitches/1/ports/65536", None),
    ],
)
def test_path_and_port_validation(
    client: TestClient, method: str, path: str, body: dict[str, object] | None
) -> None:
    assert client.request(method, path, json=body).status_code == 422


def test_domain_errors_are_sanitized(client: TestClient) -> None:
    response = client.delete("/api/v1/vswitches/10")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "resource_not_found",
            "message": "The eSwitch operation failed.",
        }
    }


def test_daemon_details_are_not_exposed() -> None:
    adapter = Mock()
    adapter.create_vswitch.side_effect = DaemonError(77, "sensitive hardware detail", 1)
    app = create_app(settings=Settings(), adapter=adapter)

    with TestClient(app) as client:
        response = client.post("/api/v1/vswitches", json={"vswitch_id": 1})

    assert response.status_code == 409
    assert "sensitive" not in response.text
    assert "77" not in response.text


def test_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "cloudstack-123"})

    assert response.headers["X-Request-ID"] == "cloudstack-123"


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "x" * 129})

    assert response.headers["X-Request-ID"] != "x" * 129
    assert response.headers["X-Request-ID"]
