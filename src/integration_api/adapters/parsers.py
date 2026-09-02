import re
from dataclasses import dataclass

from integration_api.core.exceptions import DaemonError, ResponseParseError
from integration_api.models.responses import AvailablePort, PortType

_ERR_PATTERN = re.compile(r"^ERR code=(?P<code>-?\d+) message=(?P<message>.+)$")
_REPRESENTOR_PATTERN = re.compile(
    r"^DPDK port (?P<port>\d+) \(host=(?P<host>\d+) pf=(?P<pf>\d+) vf=(?P<vf>\d+)\)$"
)
_UPLINK_PATTERN = re.compile(r"^DPDK port (?P<port>\d+) \(uplink/parent\)$")
_STATUS_SERVICE_PATTERN = re.compile(
    r"^service=(?P<service>.+) state=(?P<state>\S+) uptime=(?P<uptime>\d+)s$"
)
_STATUS_COUNTS_PATTERN = re.compile(
    r"^ports=(?P<ports>\d+) assigned=(?P<assigned>\d+) "
    r"available=(?P<available>\d+) vswitches=(?P<vswitches>\d+) fdb=(?P<fdb>\d+)$"
)


@dataclass(frozen=True, slots=True)
class SuccessEnvelope:
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StatusResponse:
    service: str
    state: str
    uptime_seconds: int
    config_path: str
    ports: int
    assigned: int
    available: int
    vswitches: int
    fdb: int


def parse_response_envelope(output: str) -> SuccessEnvelope:
    lines = output.splitlines()
    if not lines:
        raise ResponseParseError("empty eswitchctl response")
    if lines[0] == "OK":
        return SuccessEnvelope(tuple(lines[1:]))
    match = _ERR_PATTERN.fullmatch(lines[0])
    if match:
        message_lines = [match.group("message"), *lines[1:]]
        raise DaemonError(int(match.group("code")), "\n".join(message_lines))
    raise ResponseParseError("response does not start with a valid OK or ERR envelope")


def parse_mutation_response(output: str) -> None:
    envelope = parse_response_envelope(output)
    if envelope.lines:
        raise ResponseParseError("mutation response contains unexpected data")


def _parse_available_port(line: str) -> AvailablePort:
    representor = _REPRESENTOR_PATTERN.fullmatch(line)
    uplink = _UPLINK_PATTERN.fullmatch(line)
    try:
        if representor:
            return AvailablePort(
                port_id=int(representor.group("port")),
                type=PortType.REPRESENTOR,
                host=int(representor.group("host")),
                pf=int(representor.group("pf")),
                vf_index=int(representor.group("vf")),
            )
        if uplink:
            return AvailablePort(
                port_id=int(uplink.group("port")),
                type=PortType.UPLINK,
                host=None,
                pf=None,
                vf_index=None,
            )
    except ValueError as error:
        raise ResponseParseError("available-port values are outside supported ranges") from error
    raise ResponseParseError("malformed available-port response line")


def parse_available_ports(output: str) -> list[AvailablePort]:
    envelope = parse_response_envelope(output)
    ports: list[AvailablePort] = []
    seen: set[int] = set()
    for line in envelope.lines:
        port = _parse_available_port(line)
        if port.port_id in seen:
            raise ResponseParseError("duplicate port ID in available-port response")
        seen.add(port.port_id)
        ports.append(port)
    return ports


def parse_status_response(output: str) -> StatusResponse:
    envelope = parse_response_envelope(output)
    if len(envelope.lines) != 3:
        raise ResponseParseError("status response must contain exactly three data lines")
    service_match = _STATUS_SERVICE_PATTERN.fullmatch(envelope.lines[0])
    config_prefix = "config="
    counts_match = _STATUS_COUNTS_PATTERN.fullmatch(envelope.lines[2])
    if service_match is None or not envelope.lines[1].startswith(config_prefix):
        raise ResponseParseError("malformed status response")
    if counts_match is None:
        raise ResponseParseError("malformed status counts")
    config_path = envelope.lines[1][len(config_prefix) :]
    if not config_path:
        raise ResponseParseError("status config path is empty")
    return StatusResponse(
        service=service_match.group("service"),
        state=service_match.group("state"),
        uptime_seconds=int(service_match.group("uptime")),
        config_path=config_path,
        ports=int(counts_match.group("ports")),
        assigned=int(counts_match.group("assigned")),
        available=int(counts_match.group("available")),
        vswitches=int(counts_match.group("vswitches")),
        fdb=int(counts_match.group("fdb")),
    )
