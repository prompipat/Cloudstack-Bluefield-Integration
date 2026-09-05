"""Read-only dry-run VF attachment planner for captured allocation results."""

import argparse
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from host_tools.vf_pci_resolver import (
    PfMapping,
    Resolution,
    ResolverError,
    load_mapping_file,
    resolve_vf,
)

PLAN_VERSION = "1"
SUPPORTED_ALLOCATION_STATE = "PORT_ATTACHED"
MAX_IDEMPOTENCY_KEY_LENGTH = 128
DEFAULT_SYSFS_ROOT = Path("/sys/bus/pci/devices")
REQUIRED_ALLOCATION_FIELDS = frozenset(
    {
        "allocation_id",
        "idempotency_key",
        "state",
        "vswitch_id",
        "port_id",
        "host",
        "pf",
        "vf_index",
    }
)
PLAN_WARNINGS = (
    "PCI resolution confirms topology only.",
    "The plan does not prove device availability.",
    "The plan does not attach a device to a VM.",
    "The plan does not reserve or release any resource.",
)


class PlannerError(Exception):
    """Base expected planner failure with a stable machine-readable code."""

    code = "planner_error"


class AllocationFileAccessError(PlannerError):
    code = "allocation_file_access"


class MalformedAllocationJsonError(PlannerError):
    code = "malformed_allocation_json"


class InvalidAllocationPayloadError(PlannerError):
    code = "invalid_allocation_payload"


class UnsupportedAllocationStateError(PlannerError):
    code = "unsupported_allocation_state"


class UnsafePortIdentityError(PlannerError):
    code = "unsafe_port_identity"


@dataclass(frozen=True, slots=True)
class CapturedAllocation:
    allocation_id: str
    idempotency_key: str
    state: str
    vswitch_id: int
    port_id: int
    host: int
    pf: int
    vf_index: int


@dataclass(frozen=True, slots=True)
class AttachmentPlan:
    plan_version: str
    mode: str
    allocation_id: str
    idempotency_key: str
    allocation_state: str
    vswitch_id: int
    port_id: int
    host: int
    pf: int
    vf_index: int
    pf_pci_address: str
    vf_pci_address: str
    driver: str | None
    iommu_group: int | None
    vendor_id: str | None
    device_id: str | None
    vm_attachment_performed: bool
    pci_binding_changed: bool
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResolveVf(Protocol):
    def __call__(
        self,
        mappings: dict[tuple[int, int], PfMapping],
        host: int,
        pf: int,
        vf_index: int,
        *,
        sysfs_root: Path,
    ) -> Resolution: ...


def _require_string(payload: Mapping[str, object], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str):
        raise InvalidAllocationPayloadError(f"{field} must be a string")
    return value


def _require_integer(payload: Mapping[str, object], field: str) -> int:
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidAllocationPayloadError(f"{field} must be an integer")
    return value


def validate_allocation_payload(payload: object) -> CapturedAllocation:
    """Validate an exact captured allocation-result document."""
    if not isinstance(payload, dict):
        raise InvalidAllocationPayloadError("allocation payload must be a JSON object")
    fields = set(payload)
    missing = REQUIRED_ALLOCATION_FIELDS - fields
    extra = fields - REQUIRED_ALLOCATION_FIELDS
    if missing or extra:
        details = []
        if missing:
            details.append("missing required fields: " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected fields: " + ", ".join(sorted(extra)))
        raise InvalidAllocationPayloadError("; ".join(details))

    allocation_id = _require_string(payload, "allocation_id")
    try:
        parsed_id = uuid.UUID(allocation_id)
    except (ValueError, AttributeError) as error:
        raise InvalidAllocationPayloadError("allocation_id must be a canonical UUID") from error
    if str(parsed_id) != allocation_id:
        raise InvalidAllocationPayloadError("allocation_id must be a canonical UUID")

    idempotency_key = _require_string(payload, "idempotency_key")
    if (
        not idempotency_key
        or not idempotency_key.strip()
        or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH
        or not all(character.isprintable() for character in idempotency_key)
    ):
        raise InvalidAllocationPayloadError(
            f"idempotency_key must contain 1..{MAX_IDEMPOTENCY_KEY_LENGTH} printable characters"
        )

    state = _require_string(payload, "state")
    if state != SUPPORTED_ALLOCATION_STATE:
        raise UnsupportedAllocationStateError(
            f"only {SUPPORTED_ALLOCATION_STATE} allocations can be planned"
        )

    vswitch_id = _require_integer(payload, "vswitch_id")
    port_id = _require_integer(payload, "port_id")
    host = _require_integer(payload, "host")
    pf = _require_integer(payload, "pf")
    vf_index = _require_integer(payload, "vf_index")
    if not 1 <= vswitch_id <= 65535:
        raise InvalidAllocationPayloadError("vswitch_id must be in 1..65535")
    if port_id == 0:
        raise UnsafePortIdentityError("port 0 is an uplink/parent and cannot be planned")
    if not 1 <= port_id <= 65535:
        raise InvalidAllocationPayloadError("port_id must be in 1..65535")
    if host < 0 or pf < 0 or vf_index < 0:
        raise InvalidAllocationPayloadError("host, pf, and vf_index must be non-negative")

    return CapturedAllocation(
        allocation_id=allocation_id,
        idempotency_key=idempotency_key,
        state=state,
        vswitch_id=vswitch_id,
        port_id=port_id,
        host=host,
        pf=pf,
        vf_index=vf_index,
    )


def load_allocation_file(path: Path) -> CapturedAllocation:
    """Load one captured allocation result without contacting its source API."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise AllocationFileAccessError("cannot read allocation file") from error
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise MalformedAllocationJsonError("allocation file is not valid JSON") from error
    return validate_allocation_payload(payload)


def create_attachment_plan(
    allocation: CapturedAllocation,
    mappings: dict[tuple[int, int], PfMapping],
    *,
    sysfs_root: Path = DEFAULT_SYSFS_ROOT,
    resolve: ResolveVf = resolve_vf,
) -> AttachmentPlan:
    """Resolve topology and create a non-executable dry-run plan."""
    resolution = resolve(
        mappings,
        allocation.host,
        allocation.pf,
        allocation.vf_index,
        sysfs_root=sysfs_root,
    )
    return AttachmentPlan(
        plan_version=PLAN_VERSION,
        mode="dry_run",
        allocation_id=allocation.allocation_id,
        idempotency_key=allocation.idempotency_key,
        allocation_state=allocation.state,
        vswitch_id=allocation.vswitch_id,
        port_id=allocation.port_id,
        host=allocation.host,
        pf=allocation.pf,
        vf_index=allocation.vf_index,
        pf_pci_address=resolution.pf_pci_address,
        vf_pci_address=resolution.vf_pci_address,
        driver=resolution.driver,
        iommu_group=resolution.iommu_group,
        vendor_id=resolution.vendor_id,
        device_id=resolution.device_id,
        vm_attachment_performed=False,
        pci_binding_changed=False,
        warnings=PLAN_WARNINGS,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allocation-file", required=True, type=Path)
    parser.add_argument("--mapping-file", required=True, type=Path)
    parser.add_argument("--sysfs-root", type=Path, default=DEFAULT_SYSFS_ROOT)
    return parser


def _error_document(error: PlannerError | ResolverError) -> dict[str, dict[str, str]]:
    return {"error": {"code": error.code, "message": str(error)}}


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    try:
        allocation = load_allocation_file(args.allocation_file)
        plan = create_attachment_plan(
            allocation,
            load_mapping_file(args.mapping_file),
            sysfs_root=args.sysfs_root,
        )
    except (PlannerError, ResolverError) as error:
        print(json.dumps(_error_document(error), sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(plan.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
