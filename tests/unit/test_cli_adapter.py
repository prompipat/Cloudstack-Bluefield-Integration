import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from integration_api.adapters.cli import CliESwitchAdapter
from integration_api.core.exceptions import (
    AdapterOSError,
    AdapterTimeoutError,
    CommandContractError,
    DaemonError,
    ExecutableNotFoundError,
    ExecutablePermissionError,
    InvalidAdapterArgumentError,
    ResponseParseError,
)


def adapter_with_result(
    stdout: str, *, stderr: str = "", returncode: int = 0
) -> tuple[CliESwitchAdapter, Mock]:
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )
    )
    return CliESwitchAdapter(Path("/test/eswitchctl"), 3.0, runner=runner), runner


def test_create_uses_exact_argument_list_without_shell() -> None:
    adapter, runner = adapter_with_result("OK\n")

    adapter.create_vswitch(100)

    runner.assert_called_once_with(
        ["/test/eswitchctl", "vs-create", "--id", "100"],
        capture_output=True,
        text=True,
        timeout=3.0,
        check=False,
        shell=False,
    )


def test_all_operations_use_canonical_allowlisted_commands() -> None:
    adapter, runner = adapter_with_result("OK\n")
    adapter.delete_vswitch(100)
    assert runner.call_args.args[0] == ["/test/eswitchctl", "vs-delete", "--id", "100"]

    adapter, runner = adapter_with_result("OK\nDPDK port 0 (uplink/parent)\n")
    assert adapter.list_available_ports()[0].port_id == 0
    assert runner.call_args.args[0] == ["/test/eswitchctl", "list-port-available"]

    adapter, runner = adapter_with_result("OK\n")
    adapter.attach_port(100, 2)
    assert runner.call_args.args[0] == [
        "/test/eswitchctl",
        "vs-port-attach",
        "--id",
        "100",
        "--port",
        "2",
    ]

    adapter, runner = adapter_with_result("OK\n")
    adapter.detach_port(100, 2)
    assert runner.call_args.args[0] == [
        "/test/eswitchctl",
        "vs-port-detach",
        "--id",
        "100",
        "--port",
        "2",
    ]


def test_err_response_preserves_daemon_details_and_exit_code() -> None:
    adapter, _ = adapter_with_result("ERR code=7 message=Hardware failure\n", returncode=1)

    with pytest.raises(DaemonError) as caught:
        adapter.create_vswitch(1)

    assert caught.value.code == 7
    assert caught.value.message == "Hardware failure"
    assert caught.value.exit_code == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (subprocess.TimeoutExpired(["eswitchctl"], 3), AdapterTimeoutError),
        (FileNotFoundError(), ExecutableNotFoundError),
        (PermissionError(), ExecutablePermissionError),
        (OSError(), AdapterOSError),
    ],
)
def test_maps_subprocess_failures(error: BaseException, expected: type[Exception]) -> None:
    runner = Mock(side_effect=error)
    adapter = CliESwitchAdapter(Path("/test/eswitchctl"), 3, runner=runner)

    with pytest.raises(expected):
        adapter.list_available_ports()


@pytest.mark.parametrize(
    ("stdout", "returncode", "expected"),
    [
        ("OK\n", 1, CommandContractError),
        ("ERR code=2 message=bad\n", 0, CommandContractError),
        ("garbage\n", 1, AdapterOSError),
        ("garbage\n", 0, ResponseParseError),
    ],
)
def test_rejects_exit_code_and_envelope_contract_mismatch(
    stdout: str, returncode: int, expected: type[Exception]
) -> None:
    adapter, _ = adapter_with_result(stdout, returncode=returncode)

    with pytest.raises(expected):
        adapter.create_vswitch(1)


def test_readiness_uses_status_and_requires_running_state() -> None:
    adapter, runner = adapter_with_result(
        "OK\n"
        "service=eSwitch Management state=running uptime=120s\n"
        "config=/var/lib/eswitch-management/eswitch.conf\n"
        "ports=7 assigned=3 available=4 vswitches=1 fdb=2\n"
    )

    assert adapter.is_ready() is True
    assert runner.call_args.args[0] == ["/test/eswitchctl", "status"]


def test_readiness_returns_false_for_adapter_failure() -> None:
    adapter, _ = adapter_with_result(
        "eSwitch Management control socket is not available\n", returncode=1
    )

    assert adapter.is_ready() is False


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("create_vswitch", (True,)),
        ("delete_vswitch", (0,)),
        ("attach_port", (1, 65536)),
        ("detach_port", (1, False)),
    ],
)
def test_rejects_invalid_arguments_before_subprocess(method: str, args: tuple[int, ...]) -> None:
    adapter, runner = adapter_with_result("OK\n")

    with pytest.raises(InvalidAdapterArgumentError):
        getattr(adapter, method)(*args)

    runner.assert_not_called()
