from dataclasses import dataclass


class AdapterError(Exception):
    """Base class for failures at the eSwitch adapter boundary."""


@dataclass(slots=True)
class DaemonError(AdapterError):
    code: int
    message: str
    exit_code: int | None = None

    def __str__(self) -> str:
        return f"daemon error {self.code}: {self.message}"


class ResponseParseError(AdapterError):
    """The adapter received output that violates the documented CLI contract."""


class AdapterNotReadyError(AdapterError):
    """The selected adapter is not ready to serve requests."""


class VSwitchAlreadyExistsError(AdapterError):
    pass


class VSwitchNotFoundError(AdapterError):
    pass


class PortNotAvailableError(AdapterError):
    pass


class PortNotAttachedError(AdapterError):
    pass


class InvalidAdapterArgumentError(AdapterError):
    pass


class AdapterTimeoutError(AdapterError):
    pass


class ExecutableNotFoundError(AdapterError):
    pass


class ExecutablePermissionError(AdapterError):
    pass


class AdapterOSError(AdapterError):
    pass


class CommandContractError(AdapterError):
    pass
