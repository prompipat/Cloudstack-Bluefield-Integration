from collections.abc import Generator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from integration_api.adapters.mock import MockESwitchAdapter
from integration_api.core.config import AdapterMode, Settings
from integration_api.main import create_app

TOKEN = "phase-64a-deterministic-test-token-1234"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"expected_host": 1, "expected_pf": 0}


@pytest.fixture
def adapter() -> MockESwitchAdapter:
    selected = MockESwitchAdapter()
    selected.create_vswitch(101)
    return selected


@pytest.fixture
def client(adapter: MockESwitchAdapter) -> Generator[TestClient, None, None]:
    app = create_app(settings=Settings(integration_api_token=TOKEN), adapter=adapter)
    with TestClient(app) as test_client:
        yield test_client


def allocate(client: TestClient, key: str = "api-allocation-key"):
    return client.post(
        "/api/v1/vswitches/101/ports/allocate",
        json=BODY,
        headers={**AUTH, "Idempotency-Key": key},
    )


def test_authenticated_success_and_replay(client: TestClient) -> None:
    first = allocate(client)
    replay = allocate(client)
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert first.json() == {
        "allocation_id": first.json()["allocation_id"],
        "idempotency_key": "api-allocation-key",
        "state": "PORT_ATTACHED",
        "vswitch_id": 101,
        "port_id": 1,
        "host": 1,
        "pf": 0,
        "vf_index": 0,
    }


def test_missing_and_invalid_authentication_do_not_invoke_adapter(
    adapter: MockESwitchAdapter,
) -> None:
    spy = Mock(wraps=adapter)
    app = create_app(settings=Settings(integration_api_token=TOKEN), adapter=spy)
    with TestClient(app) as test_client:
        missing = test_client.post(
            "/api/v1/vswitches/101/ports/allocate",
            json=BODY,
            headers={"Idempotency-Key": "missing-auth"},
        )
        invalid = test_client.post(
            "/api/v1/vswitches/101/ports/allocate",
            json=BODY,
            headers={"Authorization": "Bearer wrong", "Idempotency-Key": "wrong-auth"},
        )
    assert missing.status_code == 401
    assert invalid.status_code == 401
    spy.list_available_ports.assert_not_called()
    spy.attach_port.assert_not_called()


@pytest.mark.parametrize("key", [None, "", "   ", "x" * 129])
def test_invalid_idempotency_key(client: TestClient, key: str | None) -> None:
    headers = dict(AUTH)
    if key is not None:
        headers["Idempotency-Key"] = key
    response = client.post("/api/v1/vswitches/101/ports/allocate", json=BODY, headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"


def test_idempotency_request_mismatch(client: TestClient) -> None:
    assert allocate(client, "mismatch-key").status_code == 201
    response = client.post(
        "/api/v1/vswitches/102/ports/allocate",
        json=BODY,
        headers={**AUTH, "Idempotency-Key": "mismatch-key"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"


def test_no_eligible_port_is_stable_error(client: TestClient) -> None:
    response = client.post(
        "/api/v1/vswitches/101/ports/allocate",
        json={"expected_host": 99, "expected_pf": 0},
        headers={**AUTH, "Idempotency-Key": "no-port-key"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_available_representor"


def test_cli_mode_fails_closed_before_adapter_invocation(adapter: MockESwitchAdapter) -> None:
    spy = Mock(wraps=adapter)
    settings = Settings(eswitch_adapter_mode=AdapterMode.CLI, integration_api_token=TOKEN)
    app = create_app(settings=settings, adapter=spy)
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/vswitches/101/ports/allocate",
            json=BODY,
            headers={**AUTH, "Idempotency-Key": "cli-disabled-key"},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "allocation_mock_only"
    spy.list_available_ports.assert_not_called()
    spy.attach_port.assert_not_called()


def test_health_and_existing_query_routes_regressions(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    available = client.get("/api/v1/ports/available", headers=AUTH)
    assert available.status_code == 200
    assert all(port["port_id"] != 1 for port in available.json()) is False
