# Host-side dry-run VF attachment planner

## Purpose and trust boundary

`host_tools.vf_attachment_planner` is a Phase 6.5A read-only reference utility
for the selected KVM Compute Host. It consumes a previously captured allocation
response file, validates the response identity, reuses the host VF-to-PCI
resolver, and emits deterministic topology evidence for a possible future
attachment workflow.

The input file must come through a trusted orchestration boundary and must be
protected from untrusted replacement. The planner does not authenticate the
file, call the Integration API, contact BlueField, or inspect CloudStack or
Libvirt. `PORT_ATTACHED` describes only the eSwitch-side workflow checkpoint;
it does not mean a VF is attached to a VM.

The planner performs no VM attachment, PCI driver binding, reservation,
release, rollback, or reconciliation. Successful output is not authorization
to attach a device and does not prove availability, ownership, reservation, or
safety.

## Captured allocation schema

The JSON object must contain exactly these fields:

| Field | Constraint |
|---|---|
| `allocation_id` | Canonical UUID string |
| `idempotency_key` | 1 through 128 printable characters |
| `state` | Exactly `PORT_ATTACHED` |
| `vswitch_id` | Integer from 1 through 65535 |
| `port_id` | Integer from 1 through 65535; port 0 is always rejected |
| `host` | Non-negative integer |
| `pf` | Non-negative integer |
| `vf_index` | Non-negative integer |

Booleans are not accepted as integers. Missing and extra fields are rejected.
The committed
[`allocation-result.example.json`](../examples/allocation-result.example.json)
uses an illustrative UUID and identifiers, not production state or a live
allocation.

## Read-only usage

Run the tool on the selected KVM Compute Host with a captured allocation file
and the reviewed site mapping:

```bash
python -m host_tools.vf_attachment_planner \
  --allocation-file /path/to/captured-allocation.json \
  --mapping-file /etc/cloudstack/bluefield-pf-map.toml \
  --sysfs-root /sys/bus/pci/devices
```

The tool passes the validated `host`, `pf`, and `vf_index` directly to
`host_tools.vf_pci_resolver`. It uses the resolver's configured PF lookup,
`virtfnN` traversal, `physfn` verification, range checking, and optional
metadata reads rather than duplicating them.

Success emits compact JSON with sorted keys. Conceptually:

```json
{
  "allocation_id": "00000000-0000-4000-8000-000000000000",
  "allocation_state": "PORT_ATTACHED",
  "device_id": "0x101e",
  "driver": "example_vfio_driver",
  "host": 1,
  "idempotency_key": "example-dry-run-allocation",
  "iommu_group": 42,
  "mode": "dry_run",
  "pci_binding_changed": false,
  "pf": 0,
  "pf_pci_address": "0000:01:00.0",
  "plan_version": "1",
  "port_id": 1,
  "vendor_id": "0x1234",
  "vf_index": 0,
  "vf_pci_address": "0000:01:00.2",
  "vm_attachment_performed": false,
  "vswitch_id": 101,
  "warnings": [
    "PCI resolution confirms topology only.",
    "The plan does not prove device availability.",
    "The plan does not attach a device to a VM.",
    "The plan does not reserve or release any resource."
  ]
}
```

The output intentionally omits `safe_to_attach` and contains no executable
command, directly applicable device XML, writable sysfs path, credential, or
Authorization header.

## Errors

Expected failures return exit status 1 and deterministic JSON with an `error`
object. Planner-owned stable codes are:

- `allocation_file_access`
- `malformed_allocation_json`
- `invalid_allocation_payload`
- `unsupported_allocation_state`
- `unsafe_port_identity`

Mapping and topology failures retain the resolver's codes, including
`missing_mapping`, `invalid_vf_range`, `broken_virtfn`, and
`mismatched_physfn`. Expected invalid input does not produce a traceback or
include the full input document in its message.

## Phase 6.5B real-host read-only validation (2026-09-05)

The planner was manually validated on the `zona-01` compute host using the
project virtual environment and temporary allocation, mapping, evidence, and
output files. The allocation document was explicitly synthetic: it was not
returned by a real allocation request, and its `PORT_ATTACHED` value was used
only to exercise the planner contract. Its correlation of DPDK port 5 with
host 1, PF 0, and VF index 4 does not prove that the port was attached,
reserved, available, or safe to attach.

The successful run exited 0 in `dry_run` mode and preserved port 5, host 1, PF
0, and VF index 4. The existing resolver returned the previously verified PF
and VF PCI topology, including the `mlx5_vfio_pci` driver and matching IOMMU,
vendor, and device metadata. Output reported `vm_attachment_performed` and
`pci_binding_changed` as false, omitted `safe_to_attach`, and included all four
warnings about topology-only evidence, availability, VM attachment, and
reservation or release.

Two negative checks exited 1 with deterministic JSON and no traceback: port 0
returned `unsafe_port_identity`, and state `ACTIVE` returned
`unsupported_allocation_state`.

SHA-256 checks confirmed that the inspected PF/VF metadata was unchanged, as
were `virtfn4` and VF `physfn` resolution. No API or network request, BlueField
or `eswitchctl` command, Libvirt operation, VM inspection, driver bind or
unbind, VFIO access, or sysfs write occurred. All temporary validation files
were removed afterward.

This result validates read-only parsing and topology planning only. It is not
attachment approval. Real allocation, VM attachment, compensation, and
reconciliation still require durable ownership evidence and explicit approval.

## Isolation and future integration

The module, example input, mapping examples, fake sysfs, and this documentation
remain outside the Integration API wheel and BlueField runtime image. The
Dockerfile copies only `src`, while `.dockerignore` excludes `host_tools`,
`examples`, `docs`, and tests. No host sysfs or VFIO mount is added to the API
container.

Actual CloudStack/Libvirt integration remains unimplemented. A future KVM Agent
may use equivalent validated topology evidence only after the durable
orchestrator confirms ownership and policy. VM attachment observation,
mutation, compensation, allocation rollback, and reconciliation remain the
orchestrator's responsibility.
