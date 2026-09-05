import ast
import json
from pathlib import Path
from typing import Any

import pytest
from host_tools import vf_attachment_planner as planner
from host_tools import vf_pci_resolver as resolver

PF_BDF = "0000:01:00.0"
VF_BDF = "0000:01:00.2"
ALLOCATION_ID = "00000000-0000-4000-8000-000000000001"


def allocation_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "allocation_id": ALLOCATION_ID,
        "idempotency_key": "planner-test-key",
        "state": "PORT_ATTACHED",
        "vswitch_id": 101,
        "port_id": 1,
        "host": 1,
        "pf": 0,
        "vf_index": 0,
    }
    payload.update(updates)
    return payload


def write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload))
    return path


def write_mapping(path: Path) -> Path:
    path.write_text('[[mappings]]\nhost = 1\npf = 0\npci_address = "0000:01:00.0"\n')
    return path


def make_sysfs(root: Path, *, metadata: bool = True) -> None:
    pf_path = root / PF_BDF
    vf_path = root / VF_BDF
    pf_path.mkdir(parents=True)
    vf_path.mkdir()
    (pf_path / "sriov_totalvfs").write_text("1\n")
    (pf_path / "virtfn0").symlink_to(vf_path)
    (vf_path / "physfn").symlink_to(pf_path)
    if metadata:
        driver = root.parent / "drivers" / "mlx5_vfio_pci"
        group = root.parent / "iommu_groups" / "137"
        driver.mkdir(parents=True)
        group.mkdir(parents=True)
        (vf_path / "driver").symlink_to(driver)
        (vf_path / "iommu_group").symlink_to(group)
        (vf_path / "vendor").write_text("0x15b3\n")
        (vf_path / "device").write_text("0x101e\n")


def validated(**updates: object) -> planner.CapturedAllocation:
    return planner.validate_allocation_payload(allocation_payload(**updates))


def snapshot(root: Path) -> list[tuple[str, str, bytes | None]]:
    result: list[tuple[str, str, bytes | None]] = []
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            result.append((relative, f"link:{path.readlink()}", None))
        elif path.is_file():
            result.append((relative, "file", path.read_bytes()))
        else:
            result.append((relative, "directory", None))
    return result


def test_successful_plan_generation_with_metadata(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)

    result = planner.create_attachment_plan(
        validated(), {(1, 0): resolver.PfMapping(1, 0, PF_BDF)}, sysfs_root=root
    )

    assert result.plan_version == "1"
    assert result.mode == "dry_run"
    assert result.allocation_state == "PORT_ATTACHED"
    assert result.pf_pci_address == PF_BDF
    assert result.vf_pci_address == VF_BDF
    assert result.driver == "mlx5_vfio_pci"
    assert result.iommu_group == 137
    assert result.vendor_id == "0x15b3"
    assert result.device_id == "0x101e"
    assert result.vm_attachment_performed is False
    assert result.pci_binding_changed is False
    assert "safe_to_attach" not in result.as_dict()
    assert len(result.warnings) == 4


def test_exact_host_pf_vf_propagation_to_resolver(tmp_path: Path) -> None:
    captured: tuple[object, ...] | None = None

    def fake_resolve(
        mappings: dict[tuple[int, int], resolver.PfMapping],
        host: int,
        pf: int,
        vf_index: int,
        *,
        sysfs_root: Path,
    ) -> resolver.Resolution:
        nonlocal captured
        captured = (mappings, host, pf, vf_index, sysfs_root)
        return resolver.Resolution(host, pf, PF_BDF, vf_index, VF_BDF, None, None, None, None)

    mappings = {(7, 3): resolver.PfMapping(7, 3, PF_BDF)}
    allocation = validated(host=7, pf=3, vf_index=9)
    planner.create_attachment_plan(allocation, mappings, sysfs_root=tmp_path, resolve=fake_resolve)

    assert captured == (mappings, 7, 3, 9, tmp_path)


def test_deterministic_cli_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    allocation = write_json(tmp_path / "allocation.json", allocation_payload())
    mapping = write_mapping(tmp_path / "mapping.toml")
    arguments = [
        "--allocation-file",
        str(allocation),
        "--mapping-file",
        str(mapping),
        "--sysfs-root",
        str(root),
    ]

    assert planner.main(arguments) == 0
    first = capsys.readouterr().out
    assert planner.main(arguments) == 0
    second = capsys.readouterr().out

    assert first == second
    result = json.loads(first)
    assert result["mode"] == "dry_run"
    assert result["vm_attachment_performed"] is False
    assert "safe_to_attach" not in result


def test_port_zero_is_rejected() -> None:
    with pytest.raises(planner.UnsafePortIdentityError):
        validated(port_id=0)


def test_missing_allocation_field_is_rejected() -> None:
    payload = allocation_payload()
    del payload["host"]
    with pytest.raises(
        planner.InvalidAllocationPayloadError, match="missing required fields: host"
    ):
        planner.validate_allocation_payload(payload)


def test_extra_allocation_field_is_rejected() -> None:
    with pytest.raises(planner.InvalidAllocationPayloadError, match="unexpected fields"):
        validated(extra="not-accepted")


@pytest.mark.parametrize(
    "field",
    ["vswitch_id", "port_id", "host", "pf", "vf_index"],
)
def test_boolean_is_not_an_integer(field: str) -> None:
    with pytest.raises(planner.InvalidAllocationPayloadError, match=f"{field} must be an integer"):
        validated(**{field: True})


@pytest.mark.parametrize("value", ["not-a-uuid", "{00000000-0000-4000-8000-000000000001}"])
def test_allocation_id_must_be_canonical_uuid(value: str) -> None:
    with pytest.raises(planner.InvalidAllocationPayloadError, match="canonical UUID"):
        validated(allocation_id=value)


@pytest.mark.parametrize("value", ["", "   ", "x" * 129, "bad\nkey"])
def test_invalid_idempotency_key(value: str) -> None:
    with pytest.raises(planner.InvalidAllocationPayloadError, match="idempotency_key"):
        validated(idempotency_key=value)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"vswitch_id": 0}, "vswitch_id"),
        ({"vswitch_id": 65536}, "vswitch_id"),
        ({"port_id": -1}, "port_id"),
        ({"port_id": 65536}, "port_id"),
        ({"host": -1}, "host, pf, and vf_index"),
        ({"pf": -1}, "host, pf, and vf_index"),
        ({"vf_index": -1}, "host, pf, and vf_index"),
    ],
)
def test_invalid_numeric_values(updates: dict[str, object], message: str) -> None:
    with pytest.raises(planner.InvalidAllocationPayloadError, match=message):
        validated(**updates)


def test_unsupported_allocation_state() -> None:
    with pytest.raises(planner.UnsupportedAllocationStateError):
        validated(state="ACTIVE")


def test_malformed_json_returns_stable_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allocation = tmp_path / "allocation.json"
    allocation.write_text("{")
    status = planner.main(
        ["--allocation-file", str(allocation), "--mapping-file", str(tmp_path / "map.toml")]
    )
    assert status == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "malformed_allocation_json"


def test_allocation_file_access_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status = planner.main(
        [
            "--allocation-file",
            str(tmp_path / "missing.json"),
            "--mapping-file",
            str(tmp_path / "map.toml"),
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert status == 1
    assert output == {
        "error": {"code": "allocation_file_access", "message": "cannot read allocation file"}
    }


def run_resolver_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], configure: Any = None
) -> dict[str, Any]:
    root = tmp_path / "devices"
    make_sysfs(root)
    if configure is not None:
        configure(root)
    allocation = write_json(tmp_path / "allocation.json", allocation_payload())
    mapping = write_mapping(tmp_path / "mapping.toml")
    status = planner.main(
        [
            "--allocation-file",
            str(allocation),
            "--mapping-file",
            str(mapping),
            "--sysfs-root",
            str(root),
        ]
    )
    assert status == 1
    return json.loads(capsys.readouterr().out)


def test_missing_mapping_preserves_resolver_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    allocation = write_json(tmp_path / "allocation.json", allocation_payload(host=9))
    mapping = write_mapping(tmp_path / "mapping.toml")
    status = planner.main(
        [
            "--allocation-file",
            str(allocation),
            "--mapping-file",
            str(mapping),
            "--sysfs-root",
            str(root),
        ]
    )
    assert status == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "missing_mapping"


def test_invalid_vf_range_preserves_resolver_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    allocation = write_json(tmp_path / "allocation.json", allocation_payload(vf_index=1))
    mapping = write_mapping(tmp_path / "mapping.toml")
    status = planner.main(
        [
            "--allocation-file",
            str(allocation),
            "--mapping-file",
            str(mapping),
            "--sysfs-root",
            str(root),
        ]
    )
    assert status == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_vf_range"


def test_broken_virtfn_preserves_resolver_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def break_link(root: Path) -> None:
        link = root / PF_BDF / "virtfn0"
        link.unlink()
        link.symlink_to(root / "0000:01:00.7")

    assert run_resolver_failure(tmp_path, capsys, break_link)["error"]["code"] == "broken_virtfn"


def test_mismatched_physfn_preserves_resolver_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def mismatch(root: Path) -> None:
        other = root / "0000:02:00.0"
        other.mkdir()
        link = root / VF_BDF / "physfn"
        link.unlink()
        link.symlink_to(other)

    assert run_resolver_failure(tmp_path, capsys, mismatch)["error"]["code"] == "mismatched_physfn"


def test_optional_metadata_may_be_absent(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    make_sysfs(root, metadata=False)
    result = planner.create_attachment_plan(
        validated(), {(1, 0): resolver.PfMapping(1, 0, PF_BDF)}, sysfs_root=root
    )
    assert (result.driver, result.iommu_group, result.vendor_id, result.device_id) == (
        None,
        None,
        None,
        None,
    )


def test_fake_sysfs_remains_byte_for_byte_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    before = snapshot(root)
    planner.create_attachment_plan(
        validated(), {(1, 0): resolver.PfMapping(1, 0, PF_BDF)}, sysfs_root=root
    )
    assert snapshot(root) == before


def test_source_has_no_execution_or_integration_capability() -> None:
    source = Path(planner.__file__).read_text()
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_roots.isdisjoint(
        {"subprocess", "socket", "httpx", "requests", "libvirt", "docker"}
    )
    forbidden = (
        "shell=True",
        "sriov_numvfs",
        "driver_override",
        "/dev/vfio",
        "eswitchctl",
        "virsh",
        "lspci",
        "safe_to_attach",
    )
    assert all(fragment not in source for fragment in forbidden)
