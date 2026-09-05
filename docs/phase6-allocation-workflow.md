# Phase 6.3: allocation and VM passthrough workflow design

## Status

Phase 6.4A implements an executable mock/fake specification of the allocation
portion of this design. It does not authorize real mutations. No real
`vs-create`, `vs-delete`, `vs-port-attach`, `vs-port-detach`, Libvirt
operation, VF binding, or VM operation may be tested without the explicit
gates in this document.

The authenticated endpoint is available only when the application is in mock
mode with an injected mock allocation service or concrete `MockESwitchAdapter`.
CLI mode fails closed with HTTP 503 and stable code `allocation_mock_only`
before an adapter call. The implementation uses development-only in-memory
persistence and a process-local lock; neither is safe across processes,
restarts, or replicas. No real mutation has been validated.

## 1. Scope and non-goals

The proposed workflow coordinates a CloudStack request with BlueField port
membership and host-local VF passthrough:

```text
CloudStack request
  -> allocate a representor and attach it to a vSwitch
  -> return host/PF/VF identity
  -> resolve the VF PCI BDF on the selected KVM host
  -> attach the VF to the VM
  -> commit, compensate, or reconcile
```

The scope is identity, orchestration state, idempotency, concurrency,
observation, and compensation. It does not redesign the DOCA pipeline, infer
PCI addresses, modify the current CLI contract, implement CloudStack changes,
or claim that sysfs resolution reserves a VF. It does not make DPDK port 0,
the uplink/parent, eligible for VM assignment.

## 2. Component responsibilities

### CloudStack Management Server

- Selects the compute host and VM and owns desired orchestration state.
- Creates a globally unique idempotency key and immutable request fingerprint.
- Stores allocation ownership, resource identity, progress, and operation
  history durably in the CloudStack database or an approved workflow store.
- Drives retry, release, and operator-visible reconciliation decisions.
- Never resolves PCI topology for a remote compute host.

### KVM Agent on the selected Compute Host

- Loads the local `(host, pf) -> PF PCI BDF` mapping.
- Resolves `vf_index` through host sysfs `virtfnN` and validates `physfn`.
- Observes Libvirt/VM PCI attachment and performs any future approved VM
  operation through the normal CloudStack agent boundary.
- Reports facts and operation results; resolution alone never reserves a VF.

### BlueField Integration API

- Authenticates controlled REST calls and validates bounded inputs.
- Maps defined operations to allowlisted `eswitchctl` argument lists.
- Normalizes daemon observations and mutation results.
- Excludes uplinks from allocation candidates and retries candidates after a
  definitive race rejection.
- Does not mount host sysfs, access Docker, run arbitrary commands, or become
  the sole durable workflow authority without an explicit storage design.

### eswitchctl

- Remains the mounted local client for the documented daemon command contract.
- Transports one allowlisted command and reports `OK`, `ERR`, or local transport
  failure; it does not provide durable workflow state.

### eswitch-management daemon

- Is authoritative for actual vSwitch existence and DPDK-port membership.
- Serializes or rejects conflicting attach operations.
- Its successful `vs-port-attach` is the current effective reservation.
- Remains operationally independent from the Integration API lifecycle.

## 3. Identity separation

| Identity | Meaning | Authority |
|---|---|---|
| DPDK `port_id` | BlueField daemon port handle | eswitch-management |
| BlueField `host` | Representor host identity | daemon inventory/configuration |
| BlueField `pf` | Representor PF identity within that host | daemon inventory/configuration |
| `vf_index` | PF-relative SR-IOV VF index | daemon identity plus host sysfs verification |
| Host PF PCI BDF | Compute-host-local PF address | reviewed host mapping configuration |
| Host VF PCI BDF | Resolved `virtfnN` target | compute-host sysfs |
| VM identity | CloudStack/Libvirt VM being served | CloudStack and Libvirt |
| vSwitch identity | BlueField logical switching domain | eswitch-management plus desired CloudStack state |

None is interchangeable. In particular, `port_id` is not `vf_index`, and a
PCI BDF must be resolved on the selected compute host rather than calculated.
Every durable allocation record should retain all applicable identities plus
the selected compute-host identity.

## 4. Proposed end-to-end sequence

1. CloudStack chooses the compute host and VM, creates an idempotency key, and
   durably records desired state `REQUESTED` with an immutable request hash.
2. It calls the authenticated atomic allocation API with vSwitch ID, expected
   BlueField host/PF identity, and optional representor constraints.
3. The API validates authentication and input, checks the idempotency record,
   and observes daemon readiness and vSwitch state. If policy permits creating
   a missing vSwitch, that action and pre-state are recorded before mutation.
4. Under a durable/distributed allocation lock for the relevant BlueField
   scope, the API lists available ports, removes uplink/parent entries, filters
   representors by expected host/PF and constraints, and chooses
   deterministically.
5. The API attempts `vs-port-attach`. Only daemon `OK` makes the attachment the
   effective reservation. A definitive already-unavailable rejection permits
   selection of another candidate; timeout or transport ambiguity does not.
6. The API durably records `port_id`, `host`, `pf`, `vf_index`, vSwitch ID,
   ownership, and attach evidence before returning success. A replay returns
   the same identity.
7. The KVM Agent resolves the returned VF index through local mapping and
   sysfs. It records the PF/VF BDF and validation evidence as a checkpoint.
8. Before attaching, the agent observes Libvirt and rejects a VF already owned
   by another VM. Any approved driver/IOMMU prerequisites remain separate,
   explicit operations outside this read-only resolver.
9. The agent performs the approved Libvirt passthrough operation and observes
   the resulting VM definition/runtime state.
10. CloudStack commits `ACTIVE` only when daemon membership and VM attachment
    both match desired ownership. Otherwise it compensates or marks
    `RECONCILIATION_REQUIRED`.
11. Release reverses only effects owned by this allocation: detach from the VM,
    verify it is absent, detach the DPDK port, and delete a vSwitch only if this
    request created it and it is proven empty.

## 5. Workflow state machine

The smallest proposed durable state set is:

| State | Meaning |
|---|---|
| `REQUESTED` | Durable intent exists; no mutation is known to have occurred. |
| `ALLOCATING` | Validation, vSwitch preparation, selection, or attach attempt is in progress. |
| `PORT_ATTACHED` | Daemon observation confirms the selected port belongs to the vSwitch. |
| `VM_ATTACHING` | PCI resolution succeeded and Libvirt attachment is in progress. |
| `ACTIVE` | Both eSwitch membership and VM PCI attachment match desired ownership. |
| `RELEASING` | Normal teardown is in progress. |
| `RELEASED` | Owned eSwitch and VM effects are confirmed absent. |
| `COMPENSATING` | Reverse operations for a failed request are in progress. |
| `FAILED` | The operation ended with a proven, stable outcome and no unknown residual effect. |
| `RECONCILIATION_REQUIRED` | Outcome or ownership is ambiguous or observed systems disagree. |

`VSWITCH_READY` and `PCI_RESOLVED` are recorded checkpoints, not workflow
states: they are repeatable observations and do not themselves establish
ownership. `ROLLBACK_PENDING` and `ROLLED_BACK` collapse into `COMPENSATING`
and the terminal `FAILED` or `RELEASED` outcome.

Workflow state is desired-progress metadata. It must be stored separately from:

- eSwitch observed state: actual vSwitch existence and port membership;
- VM/Libvirt observed state: actual VF attachment to a VM;
- sysfs observed state: current PF/VF topology, which is identity evidence and
  not reservation state.

Valid primary transitions are:

```text
REQUESTED -> ALLOCATING -> PORT_ATTACHED -> VM_ATTACHING -> ACTIVE
ACTIVE -> RELEASING -> RELEASED
ALLOCATING|PORT_ATTACHED|VM_ATTACHING -> COMPENSATING -> FAILED
any nonterminal state -> RECONCILIATION_REQUIRED
RECONCILIATION_REQUIRED -> observed safe state or operator-approved action
```

## 6. Idempotency and request IDs

`X-Request-ID` remains trace correlation and may change between retries. A
required `Idempotency-Key` represents one logical allocation or release and is
scoped to the authenticated caller and operation. The durable store retains:

- key, operation, canonical request hash, caller identity, and creation time;
- workflow state and monotonically increasing version;
- all resource identities and ownership flags;
- before/after observations, attempts, stable error code, and final response.

A replay with the same key and request hash returns the original successful
result or current operation status. Reusing a key with a different request
returns `409 IDEMPOTENCY_CONFLICT`. Concurrent requests for one key use a
unique constraint and compare-and-set state transitions.

Durable idempotency requires persistent storage shared across restarts and API
replicas. An in-memory map or lock can reduce duplicate work in one process but
cannot provide correctness. The preferred owner is the CloudStack database or
an approved durable workflow service; placing storage in the Integration API
would require an explicit availability, backup, migration, and consistency
design.

## 7. Concurrency and races

The current `list-port-available` followed by `vs-port-attach` is inherently
race-prone. Selection is advisory; the daemon's attach result is authoritative.
The allocator must:

- exclude port type `uplink` and explicitly reject `port_id=0`;
- filter to expected host/PF identity and approved constraints;
- use deterministic candidate ordering;
- serialize allocation using a durable/distributed lock or transaction scoped
  to the BlueField inventory, not only a process lock;
- attempt one candidate at a time;
- retry a different candidate only after a definitive conflict rejection;
- stop and reconcile after timeout, connection loss, malformed mutation
  response, or any outcome that could hide a successful attach.

Multiple API replicas require shared idempotency and lock state. Even with a
lock, the daemon remains the final authority because operators or other clients
may mutate it independently.

## 8. Atomic allocation design

The strongest future design is a daemon command that atomically selects and
attaches an eligible representor while recording an owner or request ID. The
DOCA owner must determine whether that command and observable ownership can be
added.

Without such a daemon command, the proposed API endpoint performs a bounded
select-and-attach loop inside one API operation. This is atomic only from the
caller's perspective after successful attach; it cannot make the list query
and attach command a single daemon transaction. Correctness therefore depends
on definitive attach rejection, durable idempotency, candidate retry, and
post-error observation.

A successful `vs-port-attach` is the effective reservation. Neither listing a
port nor resolving its VF through sysfs reserves anything. Phase 6.4A also
tracks whether attachment ownership is proven: explicit mock attach success
sets ownership, while membership observed after an ambiguous result does not.
That distinction prevents future compensation from treating observation alone
as authority to detach.

## 9. Failure and compensation matrix

| Failure | Required handling | Compensation/reconciliation |
|---|---|---|
| No eligible representor | Return stable conflict/capacity error without mutation. | None. |
| Malformed list output | Stop; do not select from partial data. | Record adapter-contract failure. |
| Attach rejected due to race | Record definitive rejection and try another eligible candidate within a bound. | None for rejected candidate. |
| Attach timeout or transport failure | Treat outcome as unknown. Never immediately repeat attach. | Observe vSwitch membership; reconcile before retry. |
| API restart after successful attach | Recover durable `ALLOCATING` record and observe daemon state. | Advance to `PORT_ATTACHED` or require reconciliation. |
| VF mapping missing | Stop before VM mutation. | Detach only the port attached by this allocation, if ownership is proven. |
| `virtfnN` missing | Stop before VM mutation. | Same owned-port compensation; flag topology error. |
| `physfn` mismatch | Treat as unsafe identity mismatch. | Do not attach VM; compensate only proven owned effects. |
| VF attached to another VM | Reject ownership conflict. | Never detach the other VM; compensate owned port if policy permits. |
| Libvirt attach failure | Observe VM definition/runtime before deciding outcome. | If proven absent, detach owned port; otherwise reconcile. |
| VM start failure | Observe whether persistent/live PCI attachment exists. | Apply CloudStack VM rollback policy, then detach only owned eSwitch effects. |
| CloudStack timeout after backend success | Replay same idempotency key. | Return stored result; do not allocate again. |
| Port detach fails during rollback | Preserve ownership record and observed attachment. | Enter `RECONCILIATION_REQUIRED`; never delete the vSwitch. |
| vSwitch deletion while ports remain | Reject deletion or observe daemon rejection. | Detach only request-owned ports; never remove foreign membership. |
| Daemon unavailable | Do not mutate or infer state. | Retry observation with backoff; retain reconciliation state. |
| Authentication failure | Return generic 401 before adapter invocation. | None. |
| Duplicate/replayed request | Compare key and request hash. | Return prior result/status or 409 on mismatch. |
| Operator intervention | Detect observed-state/version divergence. | Freeze automation and require ownership review. |

A timeout after any mutation is an unknown outcome, not proof of failure.
Observation and reconciliation are mandatory before retry or compensation.

## 10. Crash and restart reconciliation

A reconciler scans nonterminal and stale operations using leases rather than
assuming the previous worker failed before mutation. For each record it:

1. reads durable desired state, ownership flags, and last evidence;
2. observes daemon vSwitch membership through normalized query APIs;
3. asks the selected KVM Agent for sysfs and Libvirt observations;
4. compares all observations without mutating;
5. advances state only when evidence is conclusive;
6. schedules an idempotent owned compensation or marks
   `RECONCILIATION_REQUIRED` when evidence or ownership is ambiguous.

Workers use versioned compare-and-set updates and expiring leases so only one
worker acts on an operation. A restart must never turn `ALLOCATING` directly
into `FAILED`, repeat an ambiguous attach, or discard ownership history.
Operator actions are recorded as new events and never rewrite prior evidence.

## 11. Ownership and source-of-truth rules

- eswitch-management is authoritative for actual vSwitch and DPDK-port
  membership.
- KVM Agent/Libvirt is authoritative for actual VM PCI attachment.
- Host sysfs is authoritative for PF-to-VF PCI topology.
- CloudStack database or an approved durable workflow store is authoritative
  for desired state, request identity, ownership, and operation history.
- The Integration API is a controlled executor and normalized observer, not the
  sole durable source of truth unless persistent storage is explicitly built.

Each operation records whether it created the vSwitch, attached the port, and
attached the VF to the VM, plus the observed state before every mutation.
Compensation may reverse only a proven effect owned by that operation. Shared,
pre-existing, or operator-created resources are never deleted speculatively.

## 12. Security boundaries

- All `/api/v1/*` endpoints retain static Bearer authentication.
- Bearer tokens require TLS, an encrypted tunnel, or an approved protected
  management transport; Bearer authentication alone does not encrypt traffic.
- Production replaces supplementary group 0 with a least-privilege socket
  group.
- The Integration API remains unprivileged, drops all capabilities, has no
  Docker socket, and never uses host networking or an arbitrary command API.
- Host sysfs is never mounted into the BlueField API container; VF resolution
  remains in the KVM Agent on the selected compute host.
- Mapping files, tokens, authorization headers, raw operational logs, and other
  sensitive values must not appear in API responses or routine logs.
- Reconciliation and mutation permissions should be separated by caller role
  if authentication evolves beyond the current static token.

## 13. Audit logging

Every attempt records the idempotency key or safe digest, request ID, caller,
operation, workflow version, vSwitch ID, DPDK port ID, host/PF/VF identity,
compute-host and VM identity, ownership flags, prior observation, command type,
result category, stable error code, duration, and reconciliation decision.

Do not log Bearer tokens, mapping-file contents, authorization headers, raw
MAC addresses, full daemon output, or secrets. Audit events should be durable,
ordered, timestamped, and correlated across CloudStack, Integration API, and
KVM Agent without pretending clocks alone provide transaction ordering.

## 14. Proposed compatible API additions

Existing endpoints remain unchanged. New endpoints are additive and require
Bearer authentication.

### Atomic port allocation

```http
POST /api/v1/vswitches/{vswitch_id}/ports/allocate
Idempotency-Key: 01J...
Content-Type: application/json
```

```json
{
  "expected_host": 1,
  "expected_pf": 0,
  "constraints": {
    "excluded_port_ids": [],
    "allowed_vf_indices": null
  }
}
```

Successful first completion returns HTTP 201; a completed replay returns HTTP
200 with the same allocation identity:

```json
{
  "allocation_id": "01J...",
  "idempotency_key": "01J...",
  "state": "PORT_ATTACHED",
  "vswitch_id": 100,
  "port_id": 5,
  "host": 1,
  "pf": 0,
  "vf_index": 4
}
```

The implementation must exclude uplinks, never expose arbitrary command
arguments, bound retries, and persist the successful response before relying
on replay behavior.

### Allocation observation

```http
GET /api/v1/allocations/{allocation_id}
```

Returns HTTP 200 with workflow state, resource identities, ownership flags,
latest normalized eSwitch/VM observations, and a stable error when applicable.
It must not expose raw daemon output or secrets.

### Release

```http
POST /api/v1/allocations/{allocation_id}/release
Idempotency-Key: 01J...
```

Returns HTTP 202 while release is progressing, HTTP 200 when already or newly
released, and the same result for a replay. It releases only resources owned
by the allocation.

### Reconciliation

```http
POST /api/v1/allocations/{allocation_id}/reconcile
Idempotency-Key: 01J...
```

This operator-controlled endpoint returns HTTP 202 when observation work is
scheduled and HTTP 200 when a conclusive state is recorded. A future role model
should restrict it more tightly than ordinary allocation calls.

Proposed stable error codes and statuses:

| HTTP | Error code | Meaning |
|---:|---|---|
| 400 | `INVALID_ALLOCATION_REQUEST` | Invalid bounded input or constraints. |
| 401 | existing generic authentication response | Missing or invalid Bearer token. |
| 404 | `ALLOCATION_NOT_FOUND` | Unknown allocation identity. |
| 409 | `IDEMPOTENCY_CONFLICT` | Key reused with a different request. |
| 409 | `NO_AVAILABLE_REPRESENTOR` | No eligible non-uplink candidate. |
| 409 | `PORT_OWNERSHIP_CONFLICT` | Observed ownership conflicts with request. |
| 409 | `VF_VM_OWNERSHIP_CONFLICT` | VF is attached to another VM. |
| 422 | `HOST_PF_IDENTITY_MISMATCH` | Returned representor violates expected identity. |
| 503 | `DAEMON_UNAVAILABLE` | Daemon cannot be observed safely. |
| 503 | `RECONCILIATION_REQUIRED` | Mutation outcome is unknown or systems disagree. |
| 502 | `DAEMON_RESPONSE_INVALID` | Query or mutation output violates contract. |

An asynchronous implementation may return HTTP 202 plus the allocation status
resource before attachment completes. The owners must choose synchronous or
asynchronous semantics before implementation.

## 15. Questions for team owners

### CloudStack owner

- At which lifecycle event should allocation occur: VM deploy, start, NIC add,
  or a custom API?
- Where should durable operation state and idempotency records live?
- Which component chooses the vSwitch, and how does VM ownership map to vSwitch
  ownership?
- How should host migration, evacuation, restart, and failed deployment handle
  the allocation?
- Should PCI attachment be persistent, live, or both, and what observation is
  authoritative during partial VM startup?
- How are compute-host fencing and concurrent management-server workers handled?
- What API timeout and retry contract will preserve the same idempotency key?

### DOCA owner

- Can the daemon add an atomic allocate-and-attach command?
- Can it store and expose an owner or request ID in status or vSwitch listing?
- What exact states are possible when attach or detach times out?
- Are repeated attach and detach operations idempotent, and which error codes
  prove already-attached or already-detached state?
- How should stale attachments be identified without guessing from traffic?
- Can `vs-list` expose host/PF/VF identities and ownership for reconciliation?
- Does daemon serialization cover independent clients and all API replicas?
- What vSwitch IDs and representor ports are safe for controlled mutation tests?
- What observation proves a vSwitch is empty and safe to delete?

## 16. Safe staged implementation plan

1. Review this design with CloudStack, KVM, DOCA, networking, and operations
   owners; resolve every authority and lifecycle question.
2. Extend query-only parsing and normalized observation for `vs-list` without
   changing state; test entirely against fixtures.
3. Define durable schemas, unique idempotency constraints, ownership flags,
   leases, event history, and retention policy.
4. Implement the allocator against the mock adapter with deterministic race,
   timeout, crash, replay, and compensation fault injection.
5. Implement KVM-side orchestration against fake sysfs and mocked Libvirt only.
6. Add security review, authorization policy, audit redaction tests, and
   transport/deployment review.
7. If feasible, add the daemon-level atomic allocate-and-attach contract before
   relying on a client-side select/attach loop.
8. Conduct a tabletop reconciliation exercise using recorded synthetic states.
9. Obtain a separate explicit approval for a controlled real mutation test,
   with isolated resources, baseline capture, monitoring, and rollback owner.
10. Start with one approved create/attach/detach/delete lifecycle only after all
    gates pass; stop on any unknown outcome and reconcile before proceeding.

## 17. Approval gates before real mutations

All of the following require recorded approval before any real mutation:

- CloudStack and DOCA owners approve lifecycle, ownership, API, idempotency, and
  reconciliation semantics.
- Operations assigns a maintenance/change window, named operator, observer,
  rollback owner, isolated vSwitch ID, eligible representor, and stop criteria.
- The selected resources are confirmed not to carry production traffic and are
  not inferred safe merely from an available-port response or sysfs resolution.
- Pre-test eSwitch, VM, and host topology observations are captured without
  secrets, MAC addresses, or full logs.
- Durable workflow storage and replay behavior are tested across process crash
  and restart.
- Timeout-after-mutation reconciliation is demonstrated with mocks and approved
  observation commands.
- Production transport, Bearer-token custody, least-privilege socket group,
  audit retention, and access controls are approved.
- Compensation is operation-specific, idempotent, and proven never to delete or
  detach resources not created or attached by the current request.
- A final human go/no-go occurs immediately before the first mutation.

Until every gate is satisfied, work remains documentation, query-only
observation, fake sysfs, mock adapter, and mocked Libvirt testing only.
