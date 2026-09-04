# Host-side VF-to-PCI resolver

## Purpose and boundary

`host_tools.vf_pci_resolver` is a read-only Python reference for future Apache
CloudStack KVM Agent integration. The BlueField Integration API returns a DPDK
`port_id` plus `host`, `pf`, and `vf_index` for representors. It does not and
must not resolve an x86 PCI address because `/sys/bus/pci/devices` is local to
the selected KVM Compute Host. A CloudStack Management Server cannot perform
this lookup on behalf of a different compute host, even though `zona-01`
currently performs both roles.

These identifiers are distinct:

- `port_id` identifies the BlueField daemon's DPDK port;
- `vf_index` selects the PF's kernel `virtfnN` link on the compute host;
- the PCI BDF identifies the resulting host PCI function.

The observed sequence is not a mathematical formula. PCI functions cross
function and slot boundaries, so adding a VF index to a PF address is
incorrect. Always resolve the kernel-provided `virtfnN` symlink.

## Mapping configuration

The standard-library TOML format uses explicit array entries:

```toml
[[mappings]]
host = 1
pf = 0
pci_address = "0000:01:00.0"
```

The committed
[`examples/bluefield-pf-map.example.toml`](../examples/bluefield-pf-map.example.toml)
is generic and must not be treated as production state. Install a reviewed,
site-specific file on each compute host at:

```text
/etc/cloudstack/bluefield-pf-map.toml
```

Each `(host, pf)` pair must occur exactly once. Duplicate, incomplete, unknown,
or malformed mappings and non-canonical PCI addresses are rejected.

## Read-only resolution

The resolver:

1. finds the configured PF BDF for `(host, pf)`;
2. verifies the PF and readable `sriov_totalvfs` under the configured sysfs
   root (default `/sys/bus/pci/devices`);
3. validates `0 <= vf_index < sriov_totalvfs`;
4. verifies and resolves the PF's `virtfnN` symlink;
5. validates the target BDF and canonical VF device entry;
6. verifies a present `physfn` link points back to the configured PF;
7. reads optional driver, IOMMU group, vendor, and device metadata.

It performs no subprocess calls and no writes. It does not invoke `virsh`,
`lspci`, or `eswitchctl`, open VFIO devices, change SR-IOV counts, bind drivers,
or attach or detach VM devices.

## CLI usage

Run on the selected KVM Compute Host:

```bash
python -m host_tools.vf_pci_resolver \
  --mapping-file /etc/cloudstack/bluefield-pf-map.toml \
  --host 1 \
  --pf 0 \
  --vf-index 4 \
  --sysfs-root /sys/bus/pci/devices
```

Success writes deterministic JSON to standard output:

```json
{"device_id":"0x101e","driver":"mlx5_vfio_pci","host":1,"iommu_group":137,"pf":0,"pf_pci_address":"0000:84:00.0","vendor_id":"0x15b3","vf_index":4,"vf_pci_address":"0000:84:00.6"}
```

Failures return non-zero and emit structured JSON with a stable error code.
The output intentionally has no `available`, `unused`, `reserved`, or
`safe_to_attach` field. Successful resolution proves identity only, not that a
VF is free or reserved.

## Observed zona-01 mapping

The verified host/PF pair `(host=1, pf=0)` maps to PF `0000:84:00.0` on
`zona-01`. Its 16 `virtfnN` links resolve as follows:

| VF index | PCI BDF |
|---:|:---|
| 0 | `0000:84:00.2` |
| 1 | `0000:84:00.3` |
| 2 | `0000:84:00.4` |
| 3 | `0000:84:00.5` |
| 4 | `0000:84:00.6` |
| 5 | `0000:84:00.7` |
| 6 | `0000:84:01.0` |
| 7 | `0000:84:01.1` |
| 8 | `0000:84:01.2` |
| 9 | `0000:84:01.3` |
| 10 | `0000:84:01.4` |
| 11 | `0000:84:01.5` |
| 12 | `0000:84:01.6` |
| 13 | `0000:84:01.7` |
| 14 | `0000:84:02.0` |
| 15 | `0000:84:02.1` |

At inspection time, existing VM passthrough confirmed indices 2 and 3, while
the query-only candidate index 4 resolved to `0000:84:00.6`, driver
`mlx5_vfio_pci`, and single-device IOMMU group 137. This observation is not a
reservation or a claim that the VF remains unused.

## Future CloudStack integration

A later KVM Agent implementation can reuse this sequence after CloudStack
selects the compute host. Resolution alone is insufficient for allocation:
the system still needs an atomic workflow that selects and reserves a
BlueField port, resolves its host VF, attaches it, and rolls back consistently
under concurrency and failures. No real VM, VF, eSwitch, or CloudStack
operation is performed by this reference tool.
