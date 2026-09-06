# CloudStack–BlueField Integration API

> **New teammate or Codex session?** Start with the canonical
> [project handoff](docs/HANDOFF.md).

REST integration service between Apache CloudStack and NVIDIA BlueField
eSwitch Management.

The confirmed production boundary is the authenticated REST-to-`eswitchctl`
service described in the [Integration API scope](docs/integration-api-scope.md).
CloudStack, KVM Agent, Libvirt, database, and VM lifecycle changes are outside
this repository's implementation scope.

## Current status

The required production REST-to-CLI operations are implemented and tested.
Optional Phase 6 mock allocation and host-tool research is retained outside the
production boundary. The repository contains:

- strict application and adapter configuration;
- validated request and response models;
- parsers for the documented `OK`, `ERR`, status, mutation, representor-port,
  and uplink-port responses;
- deterministic mock and allowlisted CLI adapters;
- a FastAPI application with separate liveness and readiness endpoints;
- all required vSwitch and port REST endpoints, including read-only membership;
- router-level static Bearer authentication for all operational API routes;
- sanitized adapter error responses, request IDs, and operation logging;
- production-disabled interactive API documentation;
- a hardened ARM64 container definition and BlueField compose configuration;
- read-only host VF resolution and dry-run attachment-planning reference tools;
- unit, API, container-contract, and fake-CLI smoke tests.

The native ARM64 image was built and validated on `bluefield3-101` in both
mock and CLI modes. Liveness, readiness, Bearer authentication, mounted
`eswitchctl` access, read-only mounts, socket permissions, and available-port
parsing all passed. The eSwitch state was unchanged, and the independent
`eswitch-management` container remained running and healthy. Automated tests
still never invoke the production executable or contact the BlueField daemon.

## vSwitch membership observation

Authenticated `GET /api/v1/vswitches` maps only to `eswitchctl vs-list` and
returns deterministically ordered vSwitch and member-port IDs. It supports
post-timeout observation but does not prove ownership or attachment safety.
DPDK port IDs are runtime identities and must be refreshed after daemon
restart; `host`/`pf`/`vf_index` is the stable representor mapping identity.

## Runtime architecture

Development and automated tests use the mock adapter. BlueField deployment
will use a CLI adapter that invokes the host-mounted
`/usr/local/bin/eswitchctl` executable with an argument list. The container
will mount `/run/eswitch-management` so that `eswitchctl` can contact the
existing host daemon.

Direct Unix-socket communication is not the primary adapter.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Authentication and documentation

`GET /health/live` and `GET /health/ready` are unauthenticated. Every route
under `/api/v1` requires:

```text
Authorization: Bearer <INTEGRATION_API_TOKEN>
```

The token is case-sensitive and must be at least 32 characters. In CLI mode it
is required at startup. Mock mode can start without it for health-only
development, but operational requests return HTTP 401 until it is configured.
Missing and invalid credentials receive the same generic response.

Mock mode exposes `/docs`, `/redoc`, and `/openapi.json`. CLI mode disables
all three. Bearer authentication protects credentials from guessing but does
not encrypt HTTP traffic; use only an approved protected management network or
secure transport for remote access.

## Validation

```bash
ruff format --check .
ruff check .
mypy
pytest
```

The current baseline is 237 passing tests with Ruff and strict mypy also
passing.

## Optional host-side VF-to-PCI resolver reference

The optional read-only reference resolver lives outside the BlueField API
package and container. It illustrates CloudStack-owned translation of the
`host`, `pf`, and `vf_index` returned by the API through a compute-host-local
PF mapping and sysfs `virtfnN` link. See the
[VF-to-PCI resolver guide](docs/vf-pci-resolver.md).

```bash
python -m host_tools.vf_pci_resolver \
  --mapping-file examples/bluefield-pf-map.example.toml \
  --host 1 --pf 0 --vf-index 4
```

The committed mapping is deliberately generic. A real compute host should
install its site-specific mapping at
`/etc/cloudstack/bluefield-pf-map.toml`. Resolution reports identity metadata
only; it does not reserve a VF or establish that one is unused or safe to
attach. On 2026-09-05, the resolver succeeded against `zona-01` read-only
sysfs for `(host=1, pf=0, vf_index=4)` and returned the verified VF identity;
checksum and symlink checks confirmed no inspected host state changed.

## Optional allocation workflow research

The documentation-only
[Phase 6.3 allocation workflow](docs/phase6-allocation-workflow.md) defines the
future attach-as-reservation sequence, durable idempotency, concurrency model,
compensation, reconciliation, proposed API contracts, and approval gates. Phase 6.4A now implements that specification with typed domain models,
legal transitions, injected narrow interfaces, development-only in-memory
idempotency, and process-local synchronization. The authenticated allocation
atomic allocation endpoint can mutate only `MockESwitchAdapter`; CLI mode
returns HTTP 503 with `allocation_mock_only` before invoking an adapter. The
separate required create, delete, attach, and detach endpoints are
mutation-capable with the CLI adapter. No real eSwitch mutation was performed
during this validation, and those endpoints require explicit authorization,
approved isolated resources, and a rollback procedure before use.

The in-memory store and process-local lock are not safe across processes,
restarts, or replicas. They are executable test/development behavior, not a
production source of truth or distributed lock.

Phase 6.4B verified the mock workflow in a hardened, loopback-only ARM64
container. The mock adapter starts with ports but no vSwitches, so the test
first created mock vSwitch 101 and then allocated mock port 1. This changed
only in-memory mock state: uplink port 0 was excluded, idempotent replay
returned the same result, and conflicting key reuse was rejected. Only the
atomic allocation endpoint remains disabled in CLI mode. The required direct
mutation endpoints remain CLI-capable, but no real eSwitch mutation was
performed during this validation.

## Optional host-side attachment planner reference

The read-only
[host-side attachment planner](docs/vf-attachment-planner.md) validates a
previously captured `PORT_ATTACHED` allocation response, passes its
host/PF/VF identity to the existing resolver, and emits a deterministic dry-run
plan. It consumes a trusted local file and never calls the API, Libvirt,
BlueField, Docker, or a VM. Its topology result neither proves availability nor
authorizes attachment, and rollback and reconciliation remain orchestration
responsibilities.

```bash
python -m host_tools.vf_attachment_planner \
  --allocation-file examples/allocation-result.example.json \
  --mapping-file examples/bluefield-pf-map.example.toml \
  --sysfs-root /path/to/fake-or-read-only-sysfs/devices
```

The host tool and examples remain outside the API wheel and container.

Phase 6.5B manual read-only validation completed on the `zona-01` compute host
on 2026-09-05. A deliberately synthetic `PORT_ATTACHED` document exercised
identity validation and resolved the previously verified host topology. The
planner reported no VM attachment or PCI binding, rejected port 0 and an
unsupported state with stable errors, and left the inspected topology
unchanged. This was not a real allocation and does not establish that any port
or VF is available, reserved, or safe to attach.

Actual CloudStack/KVM Agent and Libvirt integration remains unimplemented. Real
allocation, VM attachment, compensation, and reconciliation require durable
ownership evidence and explicit approval.

## Optional CloudStack/KVM integration research

The documentation-only
[CloudStack/KVM integration design](docs/cloudstack-kvm-integration-design.md)
is retained only as an optional future-reference appendix for the CloudStack
team. It is not the current project plan and does not place CloudStack, KVM
Agent, Libvirt, database, or VM lifecycle changes in this project's scope. No
such behavior has been implemented or validated here.

## Phase 6 prerequisites

Before any real mutation validation or separately owned CloudStack integration
begins:

- obtain explicit approval and an operational change window for real
  `vs-create`, `vs-delete`, `vs-port-attach`, or `vs-port-detach` testing;
- agree on isolated test identifiers and ports, expected rollback steps, and
  success criteria with the eSwitch/DOCA owner;
- confirm approved encrypted transport or protected-management-network policy
  for persistent zona-01 connectivity;
- replace supplementary group 0 with a dedicated socket group for production;
- define token distribution and rotation ownership without committing secrets;
- establish monitoring and collect representative API CPU, memory, latency,
  concurrency, and failure data before selecting resource limits.

Until these gates are satisfied, continue using the query-only
[Phase 5 runbook](docs/phase5-runbook.md). Health endpoints remain public;
all `/api/v1/*` requests require Bearer authentication.

To resume development:

```bash
cd /home/prompipat/projects/cloudstack-bluefield-integration
source .venv/bin/activate
git status --short
ruff check .
mypy
pytest
```


## Container build and mock-mode validation

Build the ARM64 image:

```bash
docker buildx build \
  --platform linux/arm64 \
  --load \
  --tag cloudstack-bluefield-integration:local \
  .
```

Confirm the image architecture:

```bash
docker image inspect cloudstack-bluefield-integration:local \
  --format '{{.Architecture}}'
docker run --rm --platform linux/arm64 \
  --entrypoint uname \
  cloudstack-bluefield-integration:local -m
```

Run the API in mock mode with the production security restrictions:

```bash
docker run --detach --name eswitch-api-smoke \
  --platform linux/arm64 \
  --publish 127.0.0.1:8081:8081 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m,mode=1777 \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --user 10001:10001 \
  --env ESWITCH_ADAPTER_MODE=mock \
  cloudstack-bluefield-integration:local
curl --fail --show-error http://127.0.0.1:8081/health/ready
docker inspect eswitch-api-smoke --format '{{json .State.Health}}'
docker stop eswitch-api-smoke
docker rm eswitch-api-smoke
```

The expected readiness response is `{"status":"ready"}`. These commands do not
mount or invoke the production executable.

## Synthetic eswitchctl smoke fixture

`docker/fake-eswitchctl` supports only `--help`, `status`, and
`list-port-available`. It rejects every mutation command. It can verify
binary mounting and CLI readiness without BlueField hardware:

```bash
docker run --rm --platform linux/arm64 \
  --entrypoint /usr/local/bin/eswitchctl \
  --volume "$PWD/docker/fake-eswitchctl:/usr/local/bin/eswitchctl:ro" \
  cloudstack-bluefield-integration:local --help
docker run --rm --platform linux/arm64 \
  --entrypoint /usr/local/bin/eswitchctl \
  --volume "$PWD/docker/fake-eswitchctl:/usr/local/bin/eswitchctl:ro" \
  cloudstack-bluefield-integration:local status
```

## BlueField read-only smoke checks

Run these only after deployment to the approved BlueField host:

```bash
docker compose exec integration-api uname -m
docker compose exec integration-api /usr/local/bin/eswitchctl --help
docker compose exec integration-api /usr/local/bin/eswitchctl status
curl --fail --show-error http://127.0.0.1:8081/health/ready
```

The compose service defaults to mock mode. Set
`ESWITCH_ADAPTER_MODE=cli` only on BlueField after confirming both read-only
mounts and socket permissions. Do not run create, delete, attach, or detach
smoke commands without explicit approval.
