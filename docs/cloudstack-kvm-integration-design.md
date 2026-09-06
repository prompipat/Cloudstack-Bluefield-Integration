# Optional appendix: CloudStack and KVM Agent integration research

> **Out of current project scope.** The confirmed Integration Layer ends at
> the authenticated BlueField REST API and allowlisted `eswitchctl` adapter.
> The CloudStack team owns every CloudStack, KVM Agent, Libvirt, persistence,
> PCI-passthrough, VM-lifecycle, and rollback decision. Nothing in this
> appendix is a current Integration API requirement or implementation plan.

This audit is preserved as optional research that the CloudStack team may use
independently. The authoritative current boundary is
[`integration-api-scope.md`](integration-api-scope.md).

## 1. Scope and audited revisions

This was a source audit and hypothetical future design, not an implementation.
It identifies possible CloudStack-owned integration points without proposing
changes in the current Integration API project.

The audit used these fixed revisions:

- Integration repository: `e859eb2` on
  `feature/initial-intregration-api`.
- CloudStack audit repository:
  `e9130ba19f62ce6384180dce17f53f5c4c95aa69`, detached HEAD.
- Historical comparison only:
  `14ece7c2449247729e4fa8b24d39cf4757730c5c`, titled
  `add pci passthrough functionality`.

The pinned CloudStack revision is the authority for this document. The newer
branch containing the historical comparison commit exists, but is outside the
audit scope.

No CloudStack build or tests were run. No runtime service, database, VM,
Libvirt instance, host device, BlueField, or network endpoint was accessed.

## 2. Important source finding

The generic PCI-passthrough implementation introduced by `14ece7c2` is **not
present** at the pinned revision. `git merge-base --is-ancestor 14ece7c2
e9130ba` returned false, and the current tree contains none of its added
`pcibusaddresses`, `KVM_PCI_BUS_ADDRESSES`, `VirtualMachineTO` PCI-address
field, `attachPciDevices`, or `LibvirtGpuDef.defPci` symbols.

That historical change passed raw PCI BDFs from a deploy parameter through a
VM detail and `VirtualMachineTO` into KVM hostdev XML. It did not provide
BlueField allocation, durable ownership, idempotency, lifecycle cleanup, or
reconciliation. It is evidence of a possible data path, not current behavior
and not a design to restore unchanged.

At the pinned revision, PCI hostdev generation is implemented through the GPU
device subsystem. Existing Open vSwitch DPDK support is a separate vhost-user
network path; its `DpdkTO` is not a BlueField representor allocation model.

## 3. Source-evidence map

Paths below are relative to the audited CloudStack repository.

| Source | Symbol | Process | Relevance |
|---|---|---|---|
| `api/src/main/java/org/apache/cloudstack/api/command/user/vm/DeployVMCmd.java` | `create`, `execute` | Management server | Creates the VM request, then starts it asynchronously through `UserVmService`; no current raw PCI-BDF parameter. |
| `api/src/main/java/org/apache/cloudstack/api/command/admin/gpu/CreateGpuDeviceCmd.java` | `CreateGpuDeviceCmd` | Management server | Admin-only GPU inventory API. It requires host, bus address, card and profile IDs and optionally type, parent and NUMA data; it is not a VM allocation request. |
| `api/src/main/java/org/apache/cloudstack/api/command/admin/gpu/DiscoverGpuDevicesCmd.java` | `DiscoverGpuDevicesCmd` | Management server | Admin-only, host-ID-scoped discovery API. It demonstrates explicit role authorization but discovers GPU inventory, not BlueField resources. |
| `api/src/main/java/org/apache/cloudstack/api/command/admin/gpu/ManageGpuDeviceCmd.java` and `UnmanageGpuDeviceCmd.java` | manage/unmanage commands | Management server | Admin-only inventory lifecycle controls with device-ID lists. They do not establish VM/eSwitch ownership. |
| `api/src/main/java/org/apache/cloudstack/api/command/user/vm/StartVMCmd.java` | `execute` | Management server | Entry point for starting an existing VM. |
| `api/src/main/java/org/apache/cloudstack/api/command/user/vm/StopVMCmd.java` | `execute` | Management server | Entry point for a user stop request. |
| `api/src/main/java/org/apache/cloudstack/api/command/user/vm/DestroyVMCmd.java` | `execute` | Management server | Entry point for destroy. |
| `api/src/main/java/org/apache/cloudstack/api/command/user/vm/RebootVMCmd.java` | `execute` | Management server | Reboots an existing domain; this path does not rebuild normal start XML. |
| `api/src/main/java/org/apache/cloudstack/api/command/admin/vm/RecoverVMCmd.java` | `execute` | Management server | Restores a destroyed VM to a stopped lifecycle state; a later start uses the normal start path. |
| `server/src/main/java/com/cloud/vm/UserVmManagerImpl.java` | `startVirtualMachine(DeployVMCmd)`, `startVirtualMachine(StartVMCmd)`, `stopVirtualMachine`, `destroyVm`, `expungeVm`, `recoverVirtualMachine`, `rebootVirtualMachine` | Management server | API-to-orchestration boundary. These methods delegate lifecycle work; they are too early for host-local VF resolution because final placement may not yet be known. |
| `engine/orchestration/src/main/java/com/cloud/vm/VirtualMachineManagerImpl.java` | `advanceStart`, `orchestrateStart` | Management server | Selects a destination host, prepares network/storage, builds `VirtualMachineTO`, sends `StartCommand`, and retries failed placement. This is the principal management-side integration area. |
| same | `advanceStop`, `orchestrateStop`, `sendStop` | Management server | Sends `StopCommand` and processes confirmed or ambiguous stop results. Suitable for policy-driven release only after VM state is observed. |
| same | `destroy`, `expunge`, `advanceExpunge` | Management server | Stops and cleans VM resources. A durable BlueField allocation must not disappear before release or reconciliation finishes. |
| same | `advanceReboot`, `orchestrateReboot` | Management server | Sends `RebootCommand`; the current KVM reboot path does not recreate complete domain XML. Normally preserve the allocation. |
| same | `migrate`, `orchestrateMigrate`, `buildMigrateCommand`, `cleanup` | Management server | Prepares the destination, sends migration commands, verifies uncertain destination state, and performs cleanup. Provides precedent for destination preparation and post-timeout observation. |
| `engine/orchestration/src/main/java/com/cloud/vm/VmWorkJobVO.java` and related VM work classes | VM work job and dispatcher symbols | Management server | Existing per-VM asynchronous serialization and restartable work pattern. It does not serialize two different VMs competing for one BlueField port. |
| `engine/schema/src/main/java/com/cloud/vm/ItWorkVO.java` and its DAO | `ItWorkVO`, `updateStep` | Management server/database | Existing transient lifecycle work journal pattern. A dedicated BlueField workflow needs richer ownership and observation fields. |
| `engine/components-api/src/main/java/com/cloud/agent/AgentManager.java` | `send`, `easySend` | Management server | Typed synchronous/asynchronous command boundary to a selected host. |
| `engine/orchestration/src/main/java/com/cloud/agent/manager/AgentManagerImpl.java` | `send`, `tagCommand`, `getTimeout`, `removeAgent` | Management server | Assigns request sequence, timeout and job context; rejects remote sends inside a database transaction; handles agent disconnection and command reconciliation support. |
| `engine/orchestration/src/main/java/com/cloud/agent/manager/AgentAttache.java` | `send(Request, int)` | Management server | Waits for answers and represents timeout/late-answer ambiguity. A timed-out mutation cannot be assumed not to have happened. |
| `api/src/main/java/com/cloud/agent/api/Command.java` | command state, `isReconcile`, `executeInSequence` | Shared protocol | Existing typed, serialized command pattern and reconciliation marker. |
| `api/src/main/java/com/cloud/agent/api/Answer.java` | `result`, `details` | Shared protocol | Typed result pattern. New answers must return stable codes without credentials or sensitive payloads in details. |
| `core/src/main/java/com/cloud/agent/api/StartCommand.java` | `StartCommand` | Management server to KVM Agent | Carries `VirtualMachineTO` to the selected host. BlueField data must be structured rather than an arbitrary command or untrusted XML fragment. |
| `plugins/hypervisors/kvm/src/main/java/com/cloud/hypervisor/kvm/resource/wrapper/LibvirtStartCommandWrapper.java` | `execute` | KVM Agent | Calls `createVMFromSpec` before starting the domain. Host-local topology and ownership checks must complete before XML creation/start. |
| `plugins/hypervisors/kvm/src/main/java/com/cloud/hypervisor/kvm/resource/LibvirtComputingResource.java` | `createVMFromSpec`, `createDevicesDef`, `attachGpuDevices`, `getGpuDevices` | KVM Agent | Existing device-definition and host discovery patterns. BlueField VF resolution belongs here conceptually, through a new narrow component, not by reusing GPU classification blindly. |
| `plugins/hypervisors/kvm/src/main/java/com/cloud/hypervisor/kvm/resource/LibvirtGpuDef.java` | `generatePciXml`, `defGpu` | KVM Agent | Current PCI `<hostdev>` generation for managed GPU devices. It proves a hostdev pattern exists, not that arbitrary BlueField VFs are supported. |
| `api/src/main/java/com/cloud/agent/api/to/GPUDeviceTO.java` | `GPUDeviceTO` | Shared protocol | Existing typed transport of allocated host devices. A BlueField allocation needs its own explicit type and semantics. |
| `server/src/main/java/com/cloud/hypervisor/HypervisorGuruBase.java` | `toVirtualMachineTO` GPU-device handling | Management server | Copies allocated GPU devices into `VirtualMachineTO`; a precedent for conveying durable allocation results after placement. |
| `engine/schema/src/main/resources/META-INF/db/schema-42010to42100.sql` | `gpu_card`, `vgpu_profile`, `gpu_device` | Database | Existing persistent PCI-device inventory and allocation schema. `gpu_device` has host/bus identity, VM ownership and a host/BDF uniqueness constraint. |
| `engine/schema/src/main/java/com/cloud/gpu/GpuDeviceVO.java` | `GpuDeviceVO` | Management server/database | Current persistent device representation. BlueField data has different authorities and needs a dedicated model rather than pretending to be a GPU. |
| `engine/schema/src/main/java/com/cloud/gpu/dao/GpuDeviceDaoImpl.java` | allocation queries | Management server/database | Precedent for host-scoped resource selection and persistence. |
| `server/src/main/java/org/apache/cloudstack/gpu/GpuServiceImpl.java` | `getGPUDevice`, `allocateGpuDevicesToVmOnHost`, `deallocateGpuDevicesForVmOnHost`, `deallocateAllGpuDevicesForVm`, `discoverGpuDevices` | Management server | Existing transactional allocation/deallocation service. Useful pattern, but it cannot establish daemon-level atomicity for BlueField. |
| `plugins/hypervisors/kvm/src/main/java/com/cloud/hypervisor/kvm/resource/wrapper/LibvirtGetGPUStatsCommandWrapper.java` | `execute` | KVM Agent | Typed host discovery answer pattern. |
| `scripts/vm/hypervisor/kvm/gpudiscovery.sh` | GPU discovery script | KVM host | Current GPU inventory mechanism. It is not appropriate for BlueField VF resolution, which already has a direct, read-only sysfs resolver without subprocesses. |
| `server/src/main/java/com/cloud/hypervisor/KVMGuru.java` | `enableDpdkIfNeeded` | Management server | Marks NICs for existing OVS-DPDK vhost-user support; not PCI passthrough or BlueField ownership. |
| `plugins/hypervisors/kvm/src/main/java/com/cloud/hypervisor/kvm/resource/OvsVifDriver.java` | DPDK NIC plug/unplug methods | KVM Agent | Creates/removes OVS DPDK vhost-user ports. Do not reuse these shell-oriented semantics for the separate BlueField API. |
| `api/src/main/java/com/cloud/agent/api/to/DpdkTO.java` | `DpdkTO` | Shared protocol | Carries vhost-user path/port/mode during migration, not DPDK port ID, host/PF/VF identity, or ownership. |
| `plugins/hypervisors/kvm/src/main/java/com/cloud/hypervisor/kvm/resource/wrapper/LibvirtPrepareForMigrationCommandWrapper.java` | `execute` | KVM Agent | Captures existing vhost-user DPDK mapping during migration. It is evidence for typed migration observations only. |
| `plugins/hypervisors/kvm/src/main/java/com/cloud/hypervisor/kvm/resource/wrapper/LibvirtMigrateCommandWrapper.java` | `execute`, `replaceDpdkInterfaces`, GPU-device update helpers | KVM Agent | Destination migration/XML handling. BlueField VF migration needs an explicit device type and must not rely on generic hostdev removal. |
| `ui/src/views/compute/DeployVM.vue` | service-offering and GPU presentation | UI | Current UI exposes related offering/GPU concepts but no raw generic PCI-address field at the pinned revision. |

Relevant tests include
`server/src/test/java/org/apache/cloudstack/gpu/GpuServiceImplTest.java`,
`server/src/test/java/com/cloud/hypervisor/KVMGuruTest.java`,
`plugins/hypervisors/kvm/src/test/java/com/cloud/hypervisor/kvm/resource/LibvirtGpuDefTest.java`,
`LibvirtComputingResourceTest.java`,
`LibvirtMigrateCommandWrapperTest.java`, and
`LibvirtPrepareForMigrationCommandWrapperTest.java`. The historical raw-PCI
assertions added to `DeployVMCmdTest` are absent from the pinned tree.

## 4. Current paths and proposed ownership

### Current PCI path

The current proven PCI path is GPU-specific:

1. GPU inventory and VM assignment are stored by `GpuServiceImpl` and the
   `gpu_device` DAO/schema.
2. `HypervisorGuruBase` transfers a `GPUDeviceTO` into the VM transfer object.
3. `LibvirtStartCommandWrapper` asks `LibvirtComputingResource` to construct
   domain XML.
4. `LibvirtGpuDef` renders the PCI hostdev and VFIO driver XML.
5. GPU service hooks allocate and deallocate around VM lifecycle operations.

This path supplies useful patterns, but BlueField representors are not GPUs.
Reusing the GPU tables or DTOs would hide the distinct eSwitch membership,
host topology, ownership and compensation semantics.

### Optional future ownership model

This research scenario is not current CloudStack behavior and is not part of
the Integration API scope:

- **CloudStack Management Server:** accept policy-enabled requests; choose the
  compute host; own durable desired state, idempotency, allocation ownership,
  workflow history, retries, compensation and operator-visible status.
- **KVM Agent on the selected host:** perform read-only PF/VF sysfs resolution,
  verify local topology and Libvirt ownership, and construct the typed PCI
  hostdev definition only after authorization from the durable workflow.
- **BlueField Integration API:** authenticate an allowlisted request, perform
  normalized observation and eventually atomic allocate-and-attach/release;
  it is not the durable workflow authority.
- **`eswitchctl`:** fixed command adapter only. It is never a caller-provided
  command surface.
- **`eswitch-management`:** authority for actual vSwitch and DPDK-port
  membership.
- **Libvirt:** authority for actual VM PCI attachment.
- **Host sysfs:** authority for PF-to-VF PCI topology, not availability.

Allocation should be initiated by the Management Server after final host
selection. A new typed KVM Agent command should perform host-local resolution
and ownership observation. A standalone host service would add another
security and lifecycle boundary without source evidence that it is needed;
the existing Agent command/answer path already targets a selected host.
Initiating allocation independently inside the Agent would make CloudStack's
durable workflow lag an external mutation. That is therefore not recommended.

## 5. Identity boundaries

These identifiers must remain distinct:

| Identity | Authority | Purpose |
|---|---|---|
| DPDK port ID | eSwitch daemon | Selects an eSwitch port; port 0 and uplinks are never VM candidates. |
| BlueField host and PF | eSwitch observation/site topology | Identifies which representor family owns a VF index. |
| VF index | eSwitch observation plus PF topology | Selects `virtfnN`; it is not a PCI function arithmetic offset. |
| Host PF PCI BDF | Per-compute-host configuration | Starting point for read-only sysfs resolution. |
| Host VF PCI BDF | Host sysfs | Libvirt hostdev source identity on that compute host. |
| CloudStack host ID | CloudStack database | Selected KVM resource; not the BlueField `host` field. |
| VM ID/UUID | CloudStack/Libvirt | Allocation owner and runtime attachment target. |
| vSwitch ID | eSwitch daemon plus workflow record | eSwitch membership scope; it is not a VM identifier. |
| allocation/request ID | Durable workflow store | Idempotency, ownership, fencing and audit correlation. |

## 6. Lifecycle integration points

### Deploy and start

`DeployVMCmd.execute` and `StartVMCmd.execute` reach
`UserVmManagerImpl`, then `VirtualMachineManagerImpl.advanceStart` and
`orchestrateStart`. Placement can retry, so an allocation before the final
destination exists would be host-ambiguous.

The recommended hook is inside `orchestrateStart`, after a destination host is
selected and validated for the current attempt, but before
`hvGuru.implement(vmProfile)` creates the final `VirtualMachineTO` and before
`StartCommand` is sent. Exact placement within network/storage preparation is
an implementation decision: allocate as late as practical to shorten the
reservation window, while leaving enough time to compensate before the next
host retry.

The KVM Agent must resolve and validate the VF before
`LibvirtStartCommandWrapper.execute` calls `createVMFromSpec`. The source does
not contain a BlueField-specific hook today. A new typed preparatory
command/answer, or a structured BlueField device TO validated during the start
command, must be designed and tested. A preparatory command gives the
Management Server an explicit observation boundary before starting the VM.

### Stop

`VirtualMachineManagerImpl.orchestrateStop` sends `StopCommand` and processes
the answer. Whether an ordinary stop releases the VF is unresolved policy. If
the device must remain assigned across stop/start, keep desired allocation and
only record Libvirt detachment. If stop releases it, detach/release only after
the VM is confirmed stopped and only for the allocation owned by this VM.
Timeout requires VM observation before eSwitch release.

### Destroy and expunge

`UserVmManagerImpl.destroyVm`/`expungeVm` reach the orchestration destroy and
expunge paths, which stop and clean resources. BlueField cleanup should be an
explicit durable work item. Do not delete the ownership row when the VM row is
marked destroyed; retain it until Libvirt absence and eSwitch release are
observed, or mark `RECONCILIATION_REQUIRED`.

### Reboot and recover

The current reboot wrapper reboots the existing domain rather than rebuilding
normal start XML. Preserve the same allocation and verify it after an
ambiguous reboot. `recoverVirtualMachine` restores lifecycle state but does not
prove hardware state; the later normal start must reconcile before reuse.

### Migration and evacuation

`orchestrateMigrate` already prepares the destination, sends typed commands,
checks the destination after timeout, and cleans up. BlueField migration must
allocate and resolve a destination-host VF before migration, temporarily allow
source and destination allocations for one VM, verify the destination VM
attachment, then release the source with its original fencing generation.

The current DPDK migration mapping represents OVS vhost-user ports, not
BlueField VFs. The current GPU hostdev update logic is also not proof that an
arbitrary BlueField VF can safely migrate. Evacuation where the source is
unreachable may require deferred source reconciliation rather than immediate
release.

### Deployment failure and retry

`orchestrateStart` can retry placement on another host. Every selected host is
a separate allocation attempt. Before trying a new host, compensate the old
attempt when ownership and outcome are proven; otherwise retain its record as
`RECONCILIATION_REQUIRED`. Never carry a resolved BDF from one host to another.

## 7. Provision sequence

```mermaid
sequenceDiagram
    participant MS as Management Server
    participant DB as Durable workflow store
    participant BF as BlueField API
    participant ES as eSwitch daemon
    participant KA as Selected KVM Agent
    participant LV as Libvirt
    MS->>DB: Create intent + idempotency key
    MS->>MS: Select final compute host
    MS->>DB: Lock/CAS attempt; record ALLOCATING
    MS->>BF: Atomic allocate-and-attach request
    BF->>ES: Select eligible representor and attach
    ES-->>BF: Membership result
    BF-->>MS: port, host, PF, VF index
    MS->>DB: Record owned allocation before VM mutation
    MS->>KA: Typed resolve/ownership-check command
    KA->>KA: Resolve virtfn through read-only sysfs
    KA->>LV: Observe existing PCI ownership
    KA-->>MS: Typed BDF/observation answer
    MS->>DB: Record PCI_RESOLVED checkpoint
    MS->>KA: StartCommand with typed device identity
    KA->>LV: Generate XML and start VM
    KA-->>MS: StartAnswer
    MS->>DB: Record ACTIVE desired/observed state
```

Detailed mutation contract:

1. **Accept intent — Management Server.** Validate account/project/VM
   permission and feature policy. In one transaction create a workflow row
   with a unique scoped idempotency key and desired attachment. No external
   mutation occurs.
2. **Select host — orchestration.** Use normal deployment planning. Persist the
   CloudStack host and increment the attempt generation.
3. **Begin allocation — Management Server.** Lock/CAS the workflow and relevant
   BlueField resource scope, record `ALLOCATING`, commit, then call externally.
   Never hold a database transaction across `AgentManager.send` or an HTTP
   operation.
4. **Allocate — Integration API/daemon.** Exclude uplink/parent and port 0;
   daemon attach success is the effective reservation. The request ID is the
   idempotency boundary. A definitive race may try another candidate. Timeout
   requires observation, never blind mutation retry.
5. **Persist ownership — Management Server.** Store the returned identities,
   operation generation, and that this workflow caused the attachment. A
   conflicting unique key loses and initiates observation/compensation, not a
   second owner.
6. **Resolve — KVM Agent.** A typed command passes allocation identity and
   expected host/PF/VF. The Agent uses site PF configuration and read-only
   `virtfnN`, validates `physfn`, and checks Libvirt ownership. Resolution is a
   checkpoint, not reservation.
7. **Prepare VM mutation — Management Server.** Record `VM_ATTACHING` and the
   exact observed BDF/generation before sending `StartCommand`.
8. **Start — KVM Agent/Libvirt.** Generate a typed hostdev and start. On timeout,
   observe VM state/attachment before retry or compensation.
9. **Commit — Management Server.** Record desired `ACTIVE`, observed eSwitch
   membership and Libvirt attachment, timestamps, and the successful job.

Any unprovable external outcome becomes `RECONCILIATION_REQUIRED`.

## 8. Release sequence

Release is allocation-specific and idempotent:

1. Lock/CAS the current allocation generation and record `RELEASING` before
   mutation.
2. Confirm the relevant VM is stopped or the exact VF is absent from its
   Libvirt definition. If observation is unavailable, stop and reconcile.
3. Request Integration API release with allocation ID, expected vSwitch/port,
   ownership generation and idempotency key. Production API support does not
   exist yet.
4. After a timeout, observe eSwitch membership. Do not infer that detach failed.
5. Mark `RELEASED` only when both VM absence and eSwitch non-membership are
   observed. Preserve an append-only event record.

Destroy/expunge and deployment rollback require this sequence. Ordinary stop
and “disable passthrough” use it only after the CloudStack owner chooses the
retention policy. Never delete a vSwitch merely because one VM releases a
port. Delete only a vSwitch created and exclusively owned by the current
workflow, with zero observed members and matching generation.

## 9. Reconciliation sequence

Reconciliation is driven from durable desired state, not process memory:

1. Claim a due workflow using a lease plus generation/CAS.
2. Observe eSwitch vSwitch/port membership through normalized query APIs.
3. Ask the selected KVM Agent for read-only sysfs topology and Libvirt PCI
   attachment observations.
4. Compare three distinct facts: desired workflow state, eSwitch observed
   state, and VM/Libvirt observed state.
5. If observations agree, advance the durable checkpoint idempotently.
6. If they differ, perform only an approved mutation whose ownership and
   generation are proven. Otherwise retain `RECONCILIATION_REQUIRED` and
   produce an operator task.

Triggers include Management Server restart, Agent reconnect, Integration API
restart, daemon restart, timed-out command, stale lease, scheduled scan, and
operator request. An eSwitch attachment with no allocation record is an orphan
and must not be detached automatically. An allocation record with no eSwitch
membership may be retried only according to its last durable command and
observations. A VF attached to the wrong VM always requires reconciliation;
never “correct” it by blind detach.

## 10. Durable data model proposal

Use dedicated tables/DAOs behind a feature flag. Do not overload
`vm_instance_details`, `gpu_device`, or transient in-memory state.

### Current allocation/workflow row

- allocation UUID and scoped idempotency key;
- request fingerprint;
- owning VM ID and optional NIC ID;
- owning CloudStack compute-host ID;
- BlueField endpoint/site identifier, vSwitch ID and DPDK port ID;
- BlueField host, PF and VF index;
- configured host PF BDF reference/version and resolved VF BDF;
- workflow state and desired state;
- last observed eSwitch membership and observation time;
- last observed Libvirt attachment and observation time;
- effect markers: vSwitch created, port attached, VF/VM attached;
- generation/fencing value, lease owner and lease expiry;
- attempt count, timestamps and stable last error code;
- cleanup/compensation state.

Suggested uniqueness rules, conditioned on non-released rows where supported:

- scoped idempotency key;
- allocation UUID;
- BlueField endpoint + vSwitch + DPDK port for an active allocation;
- compute host + PF identity + VF index for an active allocation;
- compute host + resolved VF BDF for an active allocation;
- VM/NIC ownership according to the selected “one VF per attachment” policy.

Migration may legitimately create source and destination rows for one VM, so
the VM constraint must include role/generation rather than prohibit that
transition.

### Append-only operation/event table

Record request ID, allocation ID, generation, actor, operation, prior and next
workflow states, target resource identities, sanitized outcome/error code,
duration, command/answer correlation, and observation timestamps. Never store
Bearer tokens, Authorization headers, full daemon output, VM secrets, or raw
operational logs.

### Boundary of stored facts

- **Desired state:** CloudStack workflow row.
- **Ownership:** allocation row plus generation/effect markers.
- **eSwitch observed state:** timestamped normalized daemon observation.
- **VM observed state:** timestamped KVM Agent/Libvirt observation.
- **Topology:** host sysfs observation plus mapping configuration version.
- **Transient execution:** lease, attempt, current operation and timeout data.
- **Audit/history:** append-only event records.

`vm_work_job` can serialize lifecycle work for one VM, and `op_it_work` is a
useful lifecycle-journal precedent. Neither alone prevents two VMs from
claiming the same external port.

## 11. State and observation boundaries

Retain the Phase 6 state model:

`REQUESTED → ALLOCATING → PORT_ATTACHED → VM_ATTACHING → ACTIVE`

Release uses `RELEASING → RELEASED`. Owned, reversible failures use
`COMPENSATING`, then `FAILED` after proven cleanup. Any ambiguous outcome or
ownership conflict uses `RECONCILIATION_REQUIRED`.

`VSWITCH_READY` and `PCI_RESOLVED` remain timestamped observations/checkpoints,
not durable workflow states. Workflow state never substitutes for current
eSwitch or Libvirt observation. In particular, `PORT_ATTACHED` means only the
eSwitch-side allocation step was recorded; it does not mean the VF is in a VM.

## 12. Failure and compensation matrix

| Failure | Required observation | Safe response |
|---|---|---|
| No eligible representor | Available-port observation | Fail without mutation; keep request replayable per policy. |
| Malformed available list | None can be trusted | Fail closed; do not attach. |
| Definitive attach race | Fresh candidate list | Retry another eligible candidate under the same atomic operation only. |
| Attach timeout/transport loss | Actual vSwitch membership | Never blind retry. Adopt only if request ownership is provable; otherwise reconcile. |
| API restart after attach | eSwitch membership plus durable idempotency record | Replay stored result or reconcile; process memory is insufficient. |
| PF mapping or `virtfn` missing | Host sysfs topology | Keep port ownership recorded; compensate only if this workflow proved the attach. |
| `physfn` mismatch/malformed topology | Host sysfs topology | Fail closed and reconcile; no VM mutation. |
| VF already attached to another VM | Libvirt hostdev inventory | Do not detach or reuse; reconcile ownership. |
| Libvirt attach/start failure | VM definition/runtime plus eSwitch membership | Roll back the owned eSwitch attachment only after VM absence is proven. |
| VM start timeout | VM runtime and exact PCI attachment | Observe before retry, cleanup, or host reselection. |
| CloudStack timeout after backend success | eSwitch/Libvirt observations and operation record | Return/recover the idempotent result; never duplicate mutation. |
| Detach timeout/failure | eSwitch membership | Remain `RELEASING` or reconcile; do not delete ownership. |
| vSwitch deletion while ports remain | vSwitch membership and creation owner | Reject deletion; never delete a shared or unowned vSwitch. |
| Daemon unavailable | Readiness/observation later | Preserve intent and ownership; retry observation with backoff. |
| Agent disconnect/restart | Libvirt and sysfs observation after reconnect | Keep durable state; use typed command sequence and reconcile late answers. |
| Integration authentication failure | HTTP status only | Do not retry with altered credentials in workflow logs; operator/configuration failure. |
| Duplicate async job/request | Idempotency key, request fingerprint, generation | Return the same result or conflict; never duplicate allocation. |
| Operator changes external state | All authorities | Stop automatic compensation unless current ownership/generation is proven. |
| Source unavailable during migration | Destination state and deferred source observation | Do not claim source release; mark reconciliation required. |

Compensation tracks whether this operation created the vSwitch, attached the
port, or attached the VF. It restores only effects owned by that allocation
and only when the pre-mutation observation and current fencing generation still
match.

## 13. Concurrency and fencing

1. Create/request the workflow in a database transaction using a unique
   idempotency constraint and request fingerprint.
2. Acquire a row lock or compare-and-set the workflow generation and a
   BlueField allocation-scope row before external mutation.
3. Persist the next state and command intent, commit, then call the external
   component. `AgentManagerImpl` explicitly avoids holding a DB transaction
   across an agent send; the same rule should apply to the BlueField call.
4. Persist the result using a generation predicate. A stale worker that loses
   CAS may record an observation but cannot mutate or compensate.
5. Use short renewable leases for workers, but use monotonically increasing
   generations for correctness. Lease expiry alone does not prove the old
   worker stopped.
6. Serialize VM lifecycle with existing VM-work mechanisms, and separately
   serialize/uniquely constrain the BlueField port resource because competing
   requests may own different VMs.
7. Give every Agent command and Integration API mutation an operation ID and
   idempotency key tied to the durable generation. Duplicate jobs replay the
   same operation.

CloudStack locking cannot close the `list-port-available`/attach race against
other callers. The daemon or Integration API still needs a durable atomic
allocate-and-attach contract with authoritative race errors and preferably a
daemon-visible ownership/request ID. The Phase 6.4A process lock and in-memory
store are test mechanisms only.

Stale workers may never detach using only vSwitch/port identity. Detach needs
allocation ownership plus current generation and a fresh observation. Until
the daemon accepts or reports that fence, some ambiguous cases necessarily
require operator reconciliation.

## 14. API and security boundary

Only the atomic allocation endpoint remains disabled in Integration API CLI
mode. The required direct mutation endpoints are CLI-capable and require
explicit authorization, isolated resources, and rollback before use. The existing
Bearer token authenticates but does not encrypt HTTP. Production traffic must
use an approved protected management transport with TLS; plain Bearer HTTP
over an untrusted network is prohibited.

Options:

- **Protected management network plus TLS:** acceptable baseline when routes,
  firewall policy, certificate lifecycle and endpoint identity are managed.
- **Reverse proxy or service-mesh TLS termination:** acceptable if the API
  remains bound to a private interface/loopback behind it and forwarded
  identity cannot be spoofed.
- **Mutual TLS:** preferred where both Management Server and BlueField service
  identities can be provisioned and rotated; retain application authorization
  and operation-level policy.
- **SSH tunnel:** useful for controlled validation and break-glass operations,
  not the production service transport or availability design.

The production socket must use a dedicated least-privilege group rather than
supplementary group 0. The Integration API remains non-privileged, without
host networking, Docker socket, host sysfs, VFIO, hugepages, or arbitrary
command access. VF resolution remains inside the selected KVM host boundary.
Sensitive headers, credentials, full external responses and guest identifiers
must not be logged.

## 15. Optional CloudStack-team research phases

1. **Contracts and fake-agent tests:** add typed BlueField allocation/device
   DTOs, commands and answers; test lifecycle orchestration entirely with fake
   API, Agent and Libvirt observations.
2. **Durable schema/DAO behind a disabled feature flag:** implement uniqueness,
   generations, event history and restart recovery; no external mutation.
3. **KVM Agent read-only discovery:** translate the reference resolver into a
   narrow Agent component and typed command; verify sysfs and Libvirt
   observations without changing either.
4. **Mock end-to-end workflow:** exercise Management Server, fake BlueField,
   fake Agent and failure injection, including duplicate jobs and restarts.
5. **Approved isolated-resource mutation:** only after all gates, use named
   disposable vSwitch/ports and an approved non-production VM under operator
   control.
6. **Failure injection and reconciliation:** test timeouts, late answers,
   disconnects, daemon/API restart and stale fencing.
7. **Production hardening:** TLS/mTLS, dedicated socket group, monitoring,
   audit retention, capacity policy, runbooks and staged rollout.

These are suggestions only for a separately owned CloudStack initiative. Every
phase that introduces mutation would require separate explicit approval.

## 16. Non-goals

This phase does not define or implement a public CloudStack API, UI control,
schema migration, DAO, Agent command, Libvirt XML change, Integration API
mutation, daemon change, migration support, resource scheduler, or production
transport. It does not claim a resolved VF is free, reserved, owned or safe to
attach.

## 17. Open decisions

### CloudStack owner

- Which user-facing lifecycle event requests the feature: deploy, start, NIC
  add, service offering, network offering, or a separate API?
- Is allocation retained across stop/start, and how does a user disable it?
- Is ownership VM-wide or tied to a NIC/device record?
- Which Management Server service owns the workflow and feature flag?
- Should a typed read-only Agent preparation command precede `StartCommand`,
  or should `StartCommand` carry the fully checked device object?
- How are account/project permission, quota and capacity modeled?
- How should deploy retries select/compensate host-specific allocations?
- What migration and evacuation policies apply when the destination cannot
  allocate a matching VF?
- What retention and operator interfaces are required for workflow history and
  reconciliation?
- Can the current GPU hostdev renderer be generalized safely, or should a
  separate BlueField PCI definition be used?
- Which Libvirt ownership query is authoritative for stopped and running VMs?

### DOCA/eSwitch owner

- Can the daemon implement atomic allocate-and-attach directly?
- Can mutations accept and observations expose allocation/request ID and a
  fencing generation?
- Which error proves a definitive attach race, and what does timeout mean?
- Are repeated attach and detach idempotent under an operation ID?
- Can `vs-list` expose port host/PF/VF identity and ownership for
  reconciliation?
- How are stale attachments distinguished from active owners?
- What are the approved isolated test vSwitch and representor resources?
- What ordering and durability guarantees exist across daemon restart?
- Can detach reject a stale generation so an old worker cannot remove a newer
  allocation?

## 18. Exit criteria before real mutation

- Durable schema, DAO, idempotency and event history reviewed and tested.
- Distributed worker locking/CAS and generation fencing validated.
- Authoritative daemon race, timeout, replay and observation semantics agreed.
- Production atomic allocate-and-attach and ownership-aware release contract
  implemented; CLI-mode fail-closed guard remains until approval.
- Typed KVM Agent topology and Libvirt ownership observation validated
  read-only on approved hosts.
- Start, retry, stop, destroy, expunge, reboot, migration and reconnect paths
  covered by fake and failure-injection tests.
- Rollback proves effect ownership and cannot delete shared resources.
- TLS or other approved protected transport deployed with credential and
  certificate rotation.
- Dedicated eSwitch socket group configured and least privilege verified.
- Approved isolated vSwitch, ports, VM and maintenance window documented.
- CloudStack and DOCA owners explicitly approve the mutation test plan and
  rollback authority.

Until every applicable gate is met, real allocation, attach, detach and
vSwitch mutation remain prohibited.
