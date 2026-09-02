from pydantic import BaseModel, ConfigDict

from integration_api.models.common import PortId, VSwitchId


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateVSwitchRequest(StrictRequest):
    vswitch_id: VSwitchId


class AttachPortRequest(StrictRequest):
    port_id: PortId
