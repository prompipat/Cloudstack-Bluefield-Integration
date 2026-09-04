"""Read-only host VF-to-PCI resolver and command-line reference tool."""

import argparse
import json
import re
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PCI_BDF = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
PCI_ID = re.compile(r"^0x[0-9a-f]{4}$")


class ResolverError(Exception):
    """Base resolver failure with a stable machine-readable code."""

    code = "resolver_error"


class MappingFileError(ResolverError):
    code = "mapping_file_error"


class MalformedMappingError(MappingFileError):
    code = "malformed_mapping"


class DuplicateMappingError(MappingFileError):
    code = "duplicate_mapping"


class MissingMappingError(ResolverError):
    code = "missing_mapping"


class InvalidPciAddressError(ResolverError):
    code = "invalid_pci_address"


class InvalidIdentityError(ResolverError):
    code = "invalid_identity"


class MissingPfError(ResolverError):
    code = "missing_pf"


class MissingSriovTotalVfsError(ResolverError):
    code = "missing_sriov_totalvfs"


class MalformedSysfsDataError(ResolverError):
    code = "malformed_sysfs_data"


class InvalidVfRangeError(ResolverError):
    code = "invalid_vf_range"


class MissingVirtfnError(ResolverError):
    code = "missing_virtfn"


class VirtfnNotSymlinkError(ResolverError):
    code = "virtfn_not_symlink"


class BrokenVirtfnError(ResolverError):
    code = "broken_virtfn"


class MissingVfDeviceError(ResolverError):
    code = "missing_vf_device"


class MismatchedPhysfnError(ResolverError):
    code = "mismatched_physfn"


@dataclass(frozen=True, slots=True)
class PfMapping:
    host: int
    pf: int
    pci_address: str


@dataclass(frozen=True, slots=True)
class Resolution:
    host: int
    pf: int
    pf_pci_address: str
    vf_index: int
    vf_pci_address: str
    driver: str | None
    iommu_group: int | None
    vendor_id: str | None
    device_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_pci_address(value: str) -> str:
    if PCI_BDF.fullmatch(value) is None:
        raise InvalidPciAddressError(f"PCI address must use canonical dddd:bb:ss.f form: {value!r}")
    return value


def _identity(value: object, name: str, error_type: type[ResolverError]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise error_type(f"{name} must be a non-negative integer")
    return value


def load_mapping_file(path: Path) -> dict[tuple[int, int], PfMapping]:
    """Load explicit TOML mappings, rejecting malformed and duplicate keys."""
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except OSError as error:
        raise MappingFileError(f"cannot read mapping file: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise MalformedMappingError(f"invalid TOML mapping file: {path}") from error
    if set(document) != {"mappings"} or not isinstance(document["mappings"], list):
        raise MalformedMappingError("mapping file must contain only [[mappings]] entries")

    mappings: dict[tuple[int, int], PfMapping] = {}
    for index, raw in enumerate(document["mappings"]):
        if not isinstance(raw, dict) or set(raw) != {"host", "pf", "pci_address"}:
            raise MalformedMappingError(f"mappings[{index}] must contain host, pf, and pci_address")
        host = _identity(raw["host"], f"mappings[{index}].host", MalformedMappingError)
        pf = _identity(raw["pf"], f"mappings[{index}].pf", MalformedMappingError)
        pci_address = raw["pci_address"]
        if not isinstance(pci_address, str):
            raise MalformedMappingError(f"mappings[{index}].pci_address must be a string")
        try:
            validate_pci_address(pci_address)
        except InvalidPciAddressError as error:
            raise InvalidPciAddressError(
                f"mappings[{index}] has invalid PCI address: {pci_address!r}"
            ) from error
        key = (host, pf)
        if key in mappings:
            raise DuplicateMappingError(f"duplicate mapping for host={host} pf={pf}")
        mappings[key] = PfMapping(host, pf, pci_address)
    return mappings


def _read_total_vfs(path: Path) -> int:
    if not path.exists():
        raise MissingSriovTotalVfsError(f"missing sriov_totalvfs: {path}")
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError) as error:
        raise MalformedSysfsDataError(f"invalid sriov_totalvfs: {path}") from error
    if value < 0:
        raise MalformedSysfsDataError(f"invalid sriov_totalvfs: {path}")
    return value


def _optional_pci_id(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise MalformedSysfsDataError(f"cannot read {name}: {path}") from error
    if PCI_ID.fullmatch(value) is None:
        raise MalformedSysfsDataError(f"invalid {name}: {path}")
    return value


def _optional_link_name(path: Path) -> str | None:
    if not path.is_symlink():
        return None
    try:
        return path.readlink().name
    except OSError as error:
        raise MalformedSysfsDataError(f"cannot read link: {path}") from error


def _optional_iommu_group(path: Path) -> int | None:
    name = _optional_link_name(path)
    if name is None:
        return None
    try:
        value = int(name)
    except ValueError as error:
        raise MalformedSysfsDataError(f"invalid IOMMU group: {path}") from error
    if value < 0:
        raise MalformedSysfsDataError(f"invalid IOMMU group: {path}")
    return value


def resolve_vf(
    mappings: dict[tuple[int, int], PfMapping],
    host: int,
    pf: int,
    vf_index: int,
    *,
    sysfs_root: Path = Path("/sys/bus/pci/devices"),
) -> Resolution:
    """Resolve VF metadata without changing or assessing device state."""
    host = _identity(host, "host", InvalidIdentityError)
    pf = _identity(pf, "pf", InvalidIdentityError)
    vf_index = _identity(vf_index, "vf_index", InvalidVfRangeError)
    mapping = mappings.get((host, pf))
    if mapping is None:
        raise MissingMappingError(f"no mapping for host={host} pf={pf}")
    pci_address = validate_pci_address(mapping.pci_address)
    pf_path = sysfs_root / pci_address
    if not pf_path.exists():
        raise MissingPfError(f"configured PF does not exist: {pci_address}")

    total_vfs = _read_total_vfs(pf_path / "sriov_totalvfs")
    if vf_index >= total_vfs:
        raise InvalidVfRangeError(
            f"VF index {vf_index} is outside configured PF range 0..{total_vfs - 1}"
        )
    virtfn = pf_path / f"virtfn{vf_index}"
    if not virtfn.is_symlink():
        if virtfn.exists():
            raise VirtfnNotSymlinkError(f"virtfn entry is not a symlink: {virtfn}")
        raise MissingVirtfnError(f"missing virtfn entry: {virtfn}")
    try:
        vf_path = virtfn.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BrokenVirtfnError(f"broken virtfn symlink: {virtfn}") from error
    vf_pci_address = validate_pci_address(vf_path.name)
    vf_device_path = sysfs_root / vf_pci_address
    if not vf_device_path.exists():
        raise MissingVfDeviceError(f"resolved VF device does not exist: {vf_pci_address}")

    physfn = vf_device_path / "physfn"
    if physfn.is_symlink():
        try:
            matches = physfn.resolve(strict=True) == pf_path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise MalformedSysfsDataError(f"cannot resolve physfn: {physfn}") from error
        if not matches:
            raise MismatchedPhysfnError(
                f"VF physfn does not point to configured PF: {vf_pci_address}"
            )

    return Resolution(
        host=host,
        pf=pf,
        pf_pci_address=pci_address,
        vf_index=vf_index,
        vf_pci_address=vf_pci_address,
        driver=_optional_link_name(vf_device_path / "driver"),
        iommu_group=_optional_iommu_group(vf_device_path / "iommu_group"),
        vendor_id=_optional_pci_id(vf_device_path / "vendor", "vendor ID"),
        device_id=_optional_pci_id(vf_device_path / "device", "device ID"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping-file", required=True, type=Path)
    parser.add_argument("--host", required=True, type=int)
    parser.add_argument("--pf", required=True, type=int)
    parser.add_argument("--vf-index", required=True, type=int)
    parser.add_argument("--sysfs-root", type=Path, default=Path("/sys/bus/pci/devices"))
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    try:
        result = resolve_vf(
            load_mapping_file(args.mapping_file),
            args.host,
            args.pf,
            args.vf_index,
            sysfs_root=args.sysfs_root,
        )
    except ResolverError as error:
        print(
            json.dumps(
                {"error": {"code": error.code, "message": str(error)}},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
