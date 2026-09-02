import pytest

from integration_api.adapters.parsers import (
    parse_available_ports,
    parse_mutation_response,
    parse_response_envelope,
    parse_status_response,
)
from integration_api.core.exceptions import DaemonError, ResponseParseError
from integration_api.models.responses import PortType


def test_parses_ok_mutation_response() -> None:
    assert parse_mutation_response("OK\n") is None


@pytest.mark.parametrize("output", ["OK\nunexpected\n", "", "garbage\n"])
def test_rejects_malformed_mutation_response(output: str) -> None:
    with pytest.raises(ResponseParseError):
        parse_mutation_response(output)


def test_preserves_daemon_error_code_message_and_usage() -> None:
    with pytest.raises(DaemonError) as caught:
        parse_response_envelope(
            "ERR code=2 message=Invalid input\nUsage: vs-port-detach --id <id> --port <port-id>\n"
        )

    assert caught.value.code == 2
    assert caught.value.message == (
        "Invalid input\nUsage: vs-port-detach --id <id> --port <port-id>"
    )


@pytest.mark.parametrize(
    "output",
    [
        "ERR code=x message=bad\n",
        "ERR code=2\n",
        " ERR code=2 message=bad\n",
        "\nOK\n",
    ],
)
def test_rejects_malformed_error_envelopes(output: str) -> None:
    with pytest.raises(ResponseParseError):
        parse_response_envelope(output)


def test_parses_representor_and_uplink_ports() -> None:
    ports = parse_available_ports(
        "OK\nDPDK port 0 (uplink/parent)\nDPDK port 5 (host=1 pf=0 vf=4)\n"
    )

    assert ports[0].type is PortType.UPLINK
    assert ports[0].host is None
    assert ports[0].pf is None
    assert ports[0].vf_index is None
    assert ports[1].port_id == 5
    assert ports[1].type is PortType.REPRESENTOR
    assert (ports[1].host, ports[1].pf, ports[1].vf_index) == (1, 0, 4)


def test_parses_empty_available_port_list() -> None:
    assert parse_available_ports("OK\n") == []


@pytest.mark.parametrize(
    "line",
    [
        "DPDK port x (uplink/parent)",
        "DPDK port 1 (host=1 pf=0)",
        "DPDK port 1 (host=-1 pf=0 vf=0)",
        "DPDK port 1 (parent)",
    ],
)
def test_rejects_malformed_available_port_lines(line: str) -> None:
    with pytest.raises(ResponseParseError):
        parse_available_ports(f"OK\n{line}\n")


def test_rejects_duplicate_available_ports() -> None:
    with pytest.raises(ResponseParseError):
        parse_available_ports("OK\nDPDK port 1 (host=1 pf=0 vf=0)\nDPDK port 1 (uplink/parent)\n")


def test_rejects_available_port_outside_api_range() -> None:
    with pytest.raises(ResponseParseError):
        parse_available_ports("OK\nDPDK port 65536 (uplink/parent)\n")


def test_parses_status_response() -> None:
    status = parse_status_response(
        "OK\n"
        "service=eSwitch Management state=running uptime=120s\n"
        "config=/var/lib/eswitch-management/eswitch.conf\n"
        "ports=7 assigned=3 available=4 vswitches=1 fdb=2\n"
    )

    assert status.service == "eSwitch Management"
    assert status.state == "running"
    assert status.uptime_seconds == 120
    assert status.ports == 7
    assert status.available == 4


@pytest.mark.parametrize(
    "output",
    [
        "OK\n",
        "OK\nservice=eSwitch Management state=running uptime=1s\nconfig=\n"
        "ports=1 assigned=0 available=1 vswitches=0 fdb=0\n",
        "OK\nservice=eSwitch Management state=running uptime=1s\nconfig=/x\nbad\n",
    ],
)
def test_rejects_malformed_status(output: str) -> None:
    with pytest.raises(ResponseParseError):
        parse_status_response(output)
