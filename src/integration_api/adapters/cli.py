import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from integration_api.adapters.parsers import (
    parse_available_ports,
    parse_mutation_response,
    parse_response_envelope,
    parse_status_response,
)
from integration_api.core.exceptions import (
    AdapterError,
    AdapterOSError,
    AdapterTimeoutError,
    CommandContractError,
    DaemonError,
    ExecutableNotFoundError,
    ExecutablePermissionError,
    InvalidAdapterArgumentError,
    ResponseParseError,
)
from integration_api.models.responses import AvailablePort

RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def _validate_vswitch_id(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise InvalidAdapterArgumentError("vSwitch ID must be an integer from 1 through 65535")


def _validate_port_id(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
        raise InvalidAdapterArgumentError("port ID must be an integer from 0 through 65535")


class CliESwitchAdapter:
    def __init__(
        self,
        executable_path: Path,
        timeout_seconds: float,
        *,
        runner: RunCommand = subprocess.run,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        self._executable_path = executable_path
        self._timeout_seconds = timeout_seconds
        self._runner = runner

    def _execute(self, arguments: Sequence[str]) -> str:
        command = [str(self._executable_path), *arguments]
        try:
            completed = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            raise AdapterTimeoutError("eswitchctl operation timed out") from error
        except FileNotFoundError as error:
            raise ExecutableNotFoundError("eswitchctl executable is unavailable") from error
        except PermissionError as error:
            raise ExecutablePermissionError("eswitchctl executable cannot be executed") from error
        except OSError as error:
            raise AdapterOSError("eswitchctl could not be executed") from error

        try:
            envelope = parse_response_envelope(completed.stdout)
        except DaemonError as error:
            error.exit_code = completed.returncode
            if completed.returncode != 1:
                raise CommandContractError("ERR response used an unexpected exit code") from error
            raise
        except ResponseParseError as error:
            if completed.returncode != 0:
                raise AdapterOSError("eswitchctl transport or local invocation failed") from error
            raise

        if completed.returncode != 0:
            raise CommandContractError("OK response used an unexpected exit code")
        return "OK\n" + "\n".join(envelope.lines) + ("\n" if envelope.lines else "")

    def is_ready(self) -> bool:
        try:
            status = parse_status_response(self._execute(("status",)))
        except AdapterError:
            return False
        return status.state == "running"

    def create_vswitch(self, vswitch_id: int) -> None:
        _validate_vswitch_id(vswitch_id)
        parse_mutation_response(self._execute(("vs-create", "--id", str(vswitch_id))))

    def delete_vswitch(self, vswitch_id: int) -> None:
        _validate_vswitch_id(vswitch_id)
        parse_mutation_response(self._execute(("vs-delete", "--id", str(vswitch_id))))

    def list_available_ports(self) -> list[AvailablePort]:
        return parse_available_ports(self._execute(("list-port-available",)))

    def attach_port(self, vswitch_id: int, port_id: int) -> None:
        _validate_vswitch_id(vswitch_id)
        _validate_port_id(port_id)
        parse_mutation_response(
            self._execute(("vs-port-attach", "--id", str(vswitch_id), "--port", str(port_id)))
        )

    def detach_port(self, vswitch_id: int, port_id: int) -> None:
        _validate_vswitch_id(vswitch_id)
        _validate_port_id(port_id)
        parse_mutation_response(
            self._execute(("vs-port-detach", "--id", str(vswitch_id), "--port", str(port_id)))
        )
