from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from integration_api.models.common import PortId, VSwitchId


class PortType(StrEnum):
    REPRESENTOR = "representor"
    UPLINK = "uplink"


class AvailablePort(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    port_id: PortId
    type: PortType
    host: int | None
    pf: int | None
    vf_index: int | None

    @model_validator(mode="after")
    def validate_identity(self) -> "AvailablePort":
        identity = (self.host, self.pf, self.vf_index)
        if self.type is PortType.UPLINK and identity != (None, None, None):
            raise ValueError("uplink ports must not contain host/PF/VF identity")
        if self.type is PortType.REPRESENTOR and any(value is None for value in identity):
            raise ValueError("representor ports require host/PF/VF identity")
        return self


class VSwitchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    vswitch_id: VSwitchId


class PortAttachmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    vswitch_id: VSwitchId
    port_id: PortId
