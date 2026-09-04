import json
from pathlib import Path

import pytest
from host_tools import vf_pci_resolver as resolver

PF_BDF = "0000:84:00.0"
VF_BDFS = [
    "0000:84:00.2",
    "0000:84:00.3",
    "0000:84:00.4",
    "0000:84:00.5",
    "0000:84:00.6",
    "0000:84:00.7",
    "0000:84:01.0",
    "0000:84:01.1",
    "0000:84:01.2",
    "0000:84:01.3",
    "0000:84:01.4",
    "0000:84:01.5",
    "0000:84:01.6",
    "0000:84:01.7",
    "0000:84:02.0",
    "0000:84:02.1",
]


def mapping(pci_address: str = PF_BDF) -> dict[tuple[int, int], resolver.PfMapping]:
    return {(1, 0): resolver.PfMapping(1, 0, pci_address)}


def make_sysfs(root: Path, vf_bdfs: list[str] | None = None) -> Path:
    vf_bdfs = vf_bdfs or [VF_BDFS[4]]
    pf_path = root / PF_BDF
    pf_path.mkdir(parents=True)
    (pf_path / "sriov_totalvfs").write_text(str(len(vf_bdfs)))
    for index, bdf in enumerate(vf_bdfs):
        vf_path = root / bdf
        vf_path.mkdir()
        (pf_path / f"virtfn{index}").symlink_to(vf_path)
        (vf_path / "physfn").symlink_to(pf_path)
    return pf_path


def add_metadata(root: Path, bdf: str = VF_BDFS[4]) -> None:
    vf_path = root / bdf
    drivers = root.parent / "drivers"
    groups = root.parent / "iommu_groups"
    drivers.mkdir()
    groups.mkdir()
    driver = drivers / "mlx5_vfio_pci"
    group = groups / "137"
    driver.mkdir()
    group.mkdir()
    (vf_path / "driver").symlink_to(driver)
    (vf_path / "iommu_group").symlink_to(group)
    (vf_path / "vendor").write_text("0x15b3\n")
    (vf_path / "device").write_text("0x101e\n")


def write_mapping(path: Path, content: str | None = None) -> Path:
    path.write_text(
        content if content is not None else f'[[mappings]]\nhost=1\npf=0\npci_address="{PF_BDF}"\n'
    )
    return path


def test_successful_resolution_with_metadata(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    add_metadata(root)

    result = resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)

    assert result == resolver.Resolution(
        host=1,
        pf=0,
        pf_pci_address=PF_BDF,
        vf_index=0,
        vf_pci_address=VF_BDFS[4],
        driver="mlx5_vfio_pci",
        iommu_group=137,
        vendor_id="0x15b3",
        device_id="0x101e",
    )


@pytest.mark.parametrize(("vf_index", "expected"), enumerate(VF_BDFS))
def test_all_observed_vf_address_transitions(tmp_path: Path, vf_index: int, expected: str) -> None:
    root = tmp_path / "devices"
    make_sysfs(root, VF_BDFS)

    result = resolver.resolve_vf(mapping(), 1, 0, vf_index, sysfs_root=root)

    assert result.vf_pci_address == expected


def test_missing_mapping(tmp_path: Path) -> None:
    with pytest.raises(resolver.MissingMappingError):
        resolver.resolve_vf({}, 1, 0, 0, sysfs_root=tmp_path)


def test_duplicate_mapping_is_rejected(tmp_path: Path) -> None:
    config = write_mapping(
        tmp_path / "map.toml",
        f'[[mappings]]\nhost=1\npf=0\npci_address="{PF_BDF}"\n' * 2,
    )
    with pytest.raises(resolver.DuplicateMappingError):
        resolver.load_mapping_file(config)


@pytest.mark.parametrize(
    "content",
    [
        "not toml =",
        "host=1",
        "mappings={host=1}",
        "[[mappings]]\nhost=true\npf=0\npci_address='0000:01:00.0'",
    ],
)
def test_malformed_mapping_configuration(tmp_path: Path, content: str) -> None:
    with pytest.raises(resolver.MalformedMappingError):
        resolver.load_mapping_file(write_mapping(tmp_path / "map.toml", content))


def test_invalid_pf_bdf(tmp_path: Path) -> None:
    config = write_mapping(
        tmp_path / "map.toml",
        '[[mappings]]\nhost=1\npf=0\npci_address="84:00.0"\n',
    )
    with pytest.raises(resolver.InvalidPciAddressError):
        resolver.load_mapping_file(config)


@pytest.mark.parametrize("value", [-1, True])
def test_invalid_vf_identity(tmp_path: Path, value: object) -> None:
    with pytest.raises(resolver.InvalidVfRangeError):
        resolver.resolve_vf(mapping(), 1, 0, value, sysfs_root=tmp_path)  # type: ignore[arg-type]


@pytest.mark.parametrize("vf_index", [1, 2])
def test_vf_index_at_or_above_total(tmp_path: Path, vf_index: int) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    with pytest.raises(resolver.InvalidVfRangeError):
        resolver.resolve_vf(mapping(), 1, 0, vf_index, sysfs_root=root)


def test_missing_pf(tmp_path: Path) -> None:
    with pytest.raises(resolver.MissingPfError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=tmp_path)


def test_missing_sriov_totalvfs(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    (root / PF_BDF).mkdir(parents=True)
    with pytest.raises(resolver.MissingSriovTotalVfsError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)


@pytest.mark.parametrize("value", ["bad", "-1", ""])
def test_malformed_sriov_totalvfs(tmp_path: Path, value: str) -> None:
    root = tmp_path / "devices"
    pf_path = make_sysfs(root)
    (pf_path / "sriov_totalvfs").write_text(value)
    with pytest.raises(resolver.MalformedSysfsDataError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)


def test_missing_virtfn(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    pf_path = make_sysfs(root)
    (pf_path / "virtfn0").unlink()
    with pytest.raises(resolver.MissingVirtfnError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)


def test_virtfn_must_be_symlink(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    pf_path = make_sysfs(root)
    (pf_path / "virtfn0").unlink()
    (pf_path / "virtfn0").touch()
    with pytest.raises(resolver.VirtfnNotSymlinkError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)


def test_broken_virtfn_symlink(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    pf_path = make_sysfs(root)
    (pf_path / "virtfn0").unlink()
    (pf_path / "virtfn0").symlink_to(root / "0000:84:09.0")
    with pytest.raises(resolver.BrokenVirtfnError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)


def test_malformed_target_bdf(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    pf_path = make_sysfs(root)
    malformed = root / "not-a-bdf"
    malformed.mkdir()
    (pf_path / "virtfn0").unlink()
    (pf_path / "virtfn0").symlink_to(malformed)
    with pytest.raises(resolver.InvalidPciAddressError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)


def test_target_device_missing_from_canonical_sysfs_root(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    pf_path = make_sysfs(root)
    external = tmp_path / "physical" / "0000:84:03.0"
    external.mkdir(parents=True)
    (pf_path / "virtfn0").unlink()
    (pf_path / "virtfn0").symlink_to(external)
    with pytest.raises(resolver.MissingVfDeviceError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)


def test_matching_physfn_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    assert resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root).vf_index == 0


def test_mismatched_physfn_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    other_pf = root / "0000:85:00.0"
    other_pf.mkdir()
    physfn = root / VF_BDFS[4] / "physfn"
    physfn.unlink()
    physfn.symlink_to(other_pf)
    with pytest.raises(resolver.MismatchedPhysfnError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)


def test_missing_optional_driver_and_iommu_group(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    result = resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)
    assert result.driver is None
    assert result.iommu_group is None
    assert result.vendor_id is None
    assert result.device_id is None


def test_malformed_iommu_group(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    target = tmp_path / "iommu_groups" / "bad"
    target.mkdir(parents=True)
    (root / VF_BDFS[4] / "iommu_group").symlink_to(target)
    with pytest.raises(resolver.MalformedSysfsDataError):
        resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)


def test_deterministic_cli_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    config = write_mapping(tmp_path / "map.toml")
    arguments = [
        "--mapping-file",
        str(config),
        "--host",
        "1",
        "--pf",
        "0",
        "--vf-index",
        "0",
        "--sysfs-root",
        str(root),
    ]
    assert resolver.main(arguments) == 0
    first = capsys.readouterr().out
    assert resolver.main(arguments) == 0
    second = capsys.readouterr().out
    assert first == second
    payload = json.loads(first)
    assert payload["vf_pci_address"] == VF_BDFS[4]
    assert "available" not in payload and "reserved" not in payload


def test_cli_failure_is_json_and_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_mapping(tmp_path / "map.toml")
    status = resolver.main(
        [
            "--mapping-file",
            str(config),
            "--host",
            "9",
            "--pf",
            "0",
            "--vf-index",
            "0",
            "--sysfs-root",
            str(tmp_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert status == 1
    assert payload["error"]["code"] == "missing_mapping"


def test_resolution_does_not_modify_sysfs(tmp_path: Path) -> None:
    root = tmp_path / "devices"
    make_sysfs(root)
    add_metadata(root)
    before = sorted(
        (
            str(path.relative_to(root)),
            path.readlink() if path.is_symlink() else path.read_bytes() if path.is_file() else None,
        )
        for path in root.rglob("*")
    )
    resolver.resolve_vf(mapping(), 1, 0, 0, sysfs_root=root)
    after = sorted(
        (
            str(path.relative_to(root)),
            path.readlink() if path.is_symlink() else path.read_bytes() if path.is_file() else None,
        )
        for path in root.rglob("*")
    )
    assert after == before


def test_source_has_no_subprocess_or_mutation_capability() -> None:
    source = Path(resolver.__file__).read_text()
    forbidden = [
        "subprocess",
        "shell=True",
        "sriov_numvfs",
        "driver_override",
        "/dev/vfio",
        "virsh",
        "lspci",
        "eswitchctl",
    ]
    assert all(fragment not in source for fragment in forbidden)
