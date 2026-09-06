# Integration API scope and handoff contract

## Confirmed production boundary

The current project implements one narrow integration path:

```text
CloudStack client
  -> authenticated REST API
  -> Python container on BlueField
  -> allowlisted eswitchctl command
  -> existing eswitch-management daemon
```

It does not modify Apache CloudStack, the KVM Agent, Libvirt, VM lifecycle
code, or the CloudStack database. The CloudStack integration research and
host-side tools in this repository are optional references for the teams that
own those systems; they are not part of the Integration API deployment.

## In scope

The Integration API owns:

- strict REST path, body, and numeric validation;
- Bearer authentication for every `/api/v1/*` route;
- a fixed mapping from REST operations to supported `eswitchctl` arguments;
- subprocess execution with an argument list, `shell=False`, a configured
  executable path, and a bounded timeout;
- parsing documented `OK` and `ERR` envelopes and port formats;
- normalized JSON responses and sanitized client-facing errors;
- unauthenticated liveness and dependency readiness;
- hardened ARM64 container and deployment documentation.

There is no arbitrary-command endpoint. A request cannot choose an executable,
subcommand, option name, socket path, or timeout.

## Out of scope and owned by the CloudStack team

The CloudStack team decides when to call the API and owns:

- VM, NIC, deployment, stop, destroy, migration, and rollback lifecycles;
- vSwitch and port selection and higher-level allocation policy;
- durable request, ownership, idempotency, and compensation state;
- mapping returned `host`, `pf`, and `vf_index` values to host PCI BDFs;
- PCI ownership, passthrough, KVM Agent behavior, and Libvirt XML;
- CloudStack schema, APIs, permissions, UI, jobs, retries, and reconciliation.

The Integration API does not mount compute-host sysfs, manage a VM, or infer
whether a resolved VF is safe to attach.

## REST-to-CLI operation mapping

Arguments are passed as a list to mounted `/usr/local/bin/eswitchctl`.

| REST operation | Exact `eswitchctl` arguments | Success |
|---|---|---|
| `GET /api/v1/vswitches` | `vs-list` | HTTP 200 with normalized membership |
| `POST /api/v1/vswitches` with `{"vswitch_id": ID}` | `vs-create --id ID` | HTTP 201 |
| `DELETE /api/v1/vswitches/{ID}` | `vs-delete --id ID` | HTTP 204 |
| `GET /api/v1/ports/available` | `list-port-available` | HTTP 200 with normalized ports |
| `POST /api/v1/vswitches/{ID}/ports` with `{"port_id": PORT}` | `vs-port-attach --id ID --port PORT` | HTTP 200 |
| `DELETE /api/v1/vswitches/{ID}/ports/{PORT}` | `vs-port-detach --id ID --port PORT` | HTTP 204 |
| `GET /health/ready` | `status` in CLI mode | HTTP 200 when running; otherwise 503 |

The required create, delete, attach, and detach endpoints above are
mutation-capable in CLI mode. They must not be invoked without explicit
authorization, approved isolated resources, and an operation-specific rollback
procedure. No real eSwitch mutation was performed during this validation.

Only the authenticated
`POST /api/v1/vswitches/{ID}/ports/allocate` route is an executable mock-only
research specification. It is not a required production operation and returns
`allocation_mock_only` before any CLI adapter call in CLI mode.

## Validation, errors, and timeouts

vSwitch IDs are integers from 1 through 65535. Port IDs are integers from 0
through 65535; booleans and extra request fields are rejected. Whether a port
is suitable for a VM is a CloudStack policy decision.

The CLI adapter requires exit code 0 with `OK` for success and exit code 1
with `ERR` for a daemon rejection. It preserves daemon details internally
while API responses remain generic. Normalized failures include:

- HTTP 404, `resource_not_found`, for known missing resources;
- HTTP 409, `operation_conflict`, for daemon rejection or conflicts;
- HTTP 422 for request or adapter argument validation;
- HTTP 503, `adapter_unavailable`, for timeout, executable, permission, or
  operating-system execution failures;
- HTTP 502, `invalid_adapter_response`, for malformed or contradictory output.

A timeout is not proof that a mutation did not occur. The Integration API does
not compensate or infer VM state. CloudStack owns observation and rollback
before deciding whether another mutation is safe.

## Authentication and security boundary

All `/api/v1/*` routes require `Authorization: Bearer <token>`. Comparison
is constant-time; missing and invalid credentials receive the same HTTP 401 and
`WWW-Authenticate: Bearer`. Health endpoints remain public, and failed
authentication never calls the adapter.

Bearer authentication does not encrypt HTTP. Production requires TLS or an
approved protected management transport. SSH tunneling is a controlled
validation mechanism, not a production transport. Credentials and full
operational output must not be logged or committed.

## Deployment boundary

The independent, non-privileged ARM64 API container mounts only
`/usr/local/bin/eswitchctl` and `/run/eswitch-management` read-only. It must
not mount Docker, host sysfs, VFIO, hugepages, DOCA devices, daemon
configuration, or the host filesystem, and must not manage the independent
daemon container.

It runs as UID/GID 10001, drops all capabilities, enables no-new-privileges,
uses a read-only root filesystem, and has only a small `/tmp` tmpfs.
Supplementary group 0 is a PoC accommodation; production needs a dedicated
least-privilege socket group.

## Mutation-test prerequisites

This document does not authorize real mutation. Before any real create, delete,
attach, or detach test, require:

- explicit approval and a controlled change window;
- isolated vSwitch and port identifiers that cannot affect active traffic;
- pre-test observations, success criteria, and operation-specific rollback;
- an eSwitch owner monitoring service health and traffic;
- approved secure transport and secret handling;
- confirmation that the deployed CLI supports the exact command;
- post-test observation proving only intended state changed.

Mock adapters and the fake CLI remain the default mutation-test mechanisms.

## CloudStack handoff contract

CloudStack supplies selected vSwitch and port IDs with an authenticated request.
It interprets HTTP status and stable error code, correlates `X-Request-ID`,
and owns retries and lifecycle rollback. Available-port responses distinguish
representors from uplinks and return `port_id`, `host`, `pf`, and
`vf_index`; CloudStack selects a port and performs all host-local PCI work. A
caller selecting a VM VF must reject uplink/parent entries and port 0.

DPDK port IDs are runtime identities and must be refreshed after daemon
restart. The `host`/`pf`/`vf_index` tuple is the stable representor mapping
identity. Membership observation is not ownership authorization or proof that
a port or VF is safe to attach.

After timeout or transport failure, callers must not assume retrying a mutation
is harmless. A stronger idempotency or transaction contract would require a
separately approved API and authoritative daemon semantics.

## Implementation coverage

All required operations are implemented:

- API routes call the narrow `ESwitchAdapter` protocol;
- `CliESwitchAdapter` constructs every exact argument list above;
- parser tests cover `OK`, `ERR`, malformed output, status, vSwitch
  membership, representors, and uplinks;
- CLI tests assert all command lists, `shell=False`, validation, timeout,
  executable, permission, exit-code, and readiness behavior;
- API tests cover create/list/attach/detach/delete, authentication, validation,
  sanitized errors, health, and request IDs;
- lifecycle tests prove readiness uses only `status` and can recover.

There is no implementation gap for the required REST-to-CLI operations.
Remaining gaps are real mutation approval, isolated mutation resources, secure
production transport, and a dedicated socket group. Real allocation and
CloudStack orchestration remain outside this project's scope.
