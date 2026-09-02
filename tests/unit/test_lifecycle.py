import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from integration_api.adapters.cli import CliESwitchAdapter
from integration_api.core.config import AdapterMode, Settings
from integration_api.main import create_app

TEST_TOKEN = "test-token-for-cli-lifecycle-123456"

RUNNING_STATUS = (
    "OK\n"
    "service=eSwitch Management state=running uptime=120s\n"
    "config=/var/lib/eswitch-management/eswitch.conf\n"
    "ports=12 assigned=4 available=8 vswitches=1 fdb=4\n"
)


def cli_client(runner: Mock) -> TestClient:
    adapter = CliESwitchAdapter(Path("/usr/local/bin/eswitchctl"), 1, runner=runner)
    app = create_app(
        settings=Settings(
            eswitch_adapter_mode=AdapterMode.CLI,
            integration_api_token=TEST_TOKEN,
        ),
        adapter=adapter,
    )
    return TestClient(app)


def completed(stdout: str, returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["/usr/local/bin/eswitchctl", "status"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def test_cli_app_start_and_liveness_do_not_execute_dependency() -> None:
    runner = Mock(side_effect=FileNotFoundError())

    with cli_client(runner) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}
    runner.assert_not_called()


@pytest.mark.parametrize(
    "outcome",
    [
        FileNotFoundError(),
        PermissionError(),
        subprocess.TimeoutExpired(["/usr/local/bin/eswitchctl", "status"], 1),
        completed("ERR code=2 message=Unavailable\n", 1),
        completed("socket is absent\n", 1),
        completed("permission denied\n", 1),
        completed("OK\n", 1),
        completed("malformed\n", 0),
    ],
    ids=[
        "executable-absent",
        "not-executable",
        "timeout",
        "err",
        "socket-absent",
        "socket-permission",
        "unsuccessful-exit",
        "malformed-output",
    ],
)
def test_readiness_returns_503_for_every_dependency_failure(
    outcome: BaseException | subprocess.CompletedProcess[str],
) -> None:
    runner = Mock()
    if isinstance(outcome, BaseException):
        runner.side_effect = outcome
    else:
        runner.return_value = outcome

    with cli_client(runner) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert runner.call_args.args[0] == ["/usr/local/bin/eswitchctl", "status"]


def test_readiness_recovers_without_restarting_api() -> None:
    runner = Mock(
        side_effect=[
            FileNotFoundError(),
            completed(RUNNING_STATUS, 0),
        ]
    )

    with cli_client(runner) as client:
        unavailable = client.get("/health/ready")
        recovered = client.get("/health/ready")

    assert unavailable.status_code == 503
    assert recovered.status_code == 200
    assert recovered.json() == {"status": "ready"}
    assert runner.call_count == 2
    assert all(
        call.args[0] == ["/usr/local/bin/eswitchctl", "status"] for call in runner.call_args_list
    )


def test_authentication_failure_does_not_invoke_cli_adapter() -> None:
    runner = Mock(
        return_value=completed(
            "OK\nDPDK port 0 (uplink/parent)\n",
            0,
        )
    )

    with cli_client(runner) as client:
        response = client.get(
            "/api/v1/ports/available",
            headers={"Authorization": "Bearer wrong-token-value-that-is-long-enough"},
        )

    assert response.status_code == 401
    runner.assert_not_called()
