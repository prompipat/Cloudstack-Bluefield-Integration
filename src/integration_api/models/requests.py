from pydantic import BaseModel, ConfigDict, Field

from integration_api.models.common import NonNegativeIdentity, PortId, VSwitchId


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateVSwitchRequest(StrictRequest):
    vswitch_id: VSwitchId


class AttachPortRequest(StrictRequest):
    port_id: PortId


class AllocationConstraintsRequest(StrictRequest):
    excluded_port_ids: list[PortId] = Field(default_factory=list)
    allowed_vf_indices: list[NonNegativeIdentity] | None = None


class AllocatePortRequest(StrictRequest):
    expected_host: NonNegativeIdentity
    expected_pf: NonNegativeIdentity
    constraints: AllocationConstraintsRequest | None = None
