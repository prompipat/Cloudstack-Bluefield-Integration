import pytest
from pydantic import ValidationError

from integration_api.models.requests import AttachPortRequest, CreateVSwitchRequest
from integration_api.models.responses import AvailablePort, PortType


@pytest.mark.parametrize("value", [1, 65535])
def test_vswitch_id_accepts_boundaries(value: int) -> None:
    assert CreateVSwitchRequest(vswitch_id=value).vswitch_id == value


@pytest.mark.parametrize("value", [0, -1, 65536, True, False, 1.0, "1"])
def test_vswitch_id_rejects_invalid_or_non_strict_values(value: object) -> None:
    with pytest.raises(ValidationError):
        CreateVSwitchRequest(vswitch_id=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, 65535])
def test_port_id_accepts_boundaries(value: int) -> None:
    assert AttachPortRequest(port_id=value).port_id == value


@pytest.mark.parametrize("value", [-1, 65536, True, False, 1.0, "1"])
def test_port_id_rejects_invalid_or_non_strict_values(value: object) -> None:
    with pytest.raises(ValidationError):
        AttachPortRequest(port_id=value)  # type: ignore[arg-type]


def test_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CreateVSwitchRequest(vswitch_id=1, command="status")  # type: ignore[call-arg]


def test_available_port_enforces_representor_identity() -> None:
    with pytest.raises(ValidationError):
        AvailablePort(
            port_id=1,
            type=PortType.REPRESENTOR,
            host=1,
            pf=None,
            vf_index=0,
        )


def test_available_port_enforces_empty_uplink_identity() -> None:
    with pytest.raises(ValidationError):
        AvailablePort(
            port_id=0,
            type=PortType.UPLINK,
            host=1,
            pf=None,
            vf_index=None,
        )
