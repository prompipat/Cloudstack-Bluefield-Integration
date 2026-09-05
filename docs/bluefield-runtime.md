# BlueField Runtime Information

## Host

- Hostname: `bluefield3-101`
- Operating system: Ubuntu 24.04.4 LTS
- Architecture: ARM64/aarch64
- Host glibc: 2.39

## eswitchctl

- Path: `/usr/local/bin/eswitchctl`
- Format: ARM aarch64 dynamically linked ELF
- Owner: `root`
- Group: `root`
- Mode: `0755`
- Dependencies:
  - `/lib/aarch64-linux-gnu/libc.so.6`
  - `/lib/ld-linux-aarch64.so.1`
- Maximum required GLIBC symbol: `GLIBC_2.34`

## Control socket

- Path: `/run/eswitch-management/control.sock`
- Type: Unix stream socket
- Owner: `root`
- Group: `root`
- Mode: `0660`

## Container implications

- Target runtime platform is `linux/arm64`.
- `python:3.12-slim-bookworm` is compatible because it provides GLIBC 2.36.
- Mount `/usr/local/bin/eswitchctl` read-only.
- Mount `/run/eswitch-management` read-only.
- The PoC container uses supplementary group 0 for socket access.
- Production should use a dedicated socket group.

## Existing eSwitch runtime

- The daemon runs as the standalone Docker container `eswitch-management`
  from image `eswitch-management:01-09`.
- It is not a systemd service and is not part of the Integration API Compose
  project.
- Its restart policy is `unless-stopped` and its network mode is `host`.
- It bind-mounts `/run/eswitch-management` read-write, which exposes the
  control socket through the host directory mounted read-only by the API.
- Its healthcheck already runs `/usr/local/bin/eswitchctl status`.
- It separately owns the hugepages, DOCA/DPDK devices, and persistent eSwitch
  configuration required by the data plane.

The Integration API is an independent, unprivileged client. It mounts only the
host executable and control-socket directory and must not manage or share the
daemon container's lifecycle, host networking, hugepages, device access,
configuration mount, or Docker socket.

## Operational state and Phase 5 safety

The verified environment carries active traffic on vSwitch 100. At the time
of observation the daemon reported 12 ports, 4 assigned ports, 8 available
ports, one vSwitch, and 4 FDB entries. Treat these counts as a comparison
baseline rather than desired configuration.

Phase 5 is query-only. Do not run create, delete, attach, or detach operations.
Do not commit raw MAC addresses or complete daemon logs. Repeated FDB removal
retry messages have been observed; they are an operational note for the
daemon/DOCA owner and must not be diagnosed or remediated by this Integration
API.

Docker currently reports approximately 15.3 GiB RAM on the host. The daemon
uses approximately 68 MiB and roughly one CPU core due to DPDK polling, without
an explicit cpuset or NanoCPUs limit. Do not derive API resource limits from
these observations; measure API CPU, RSS, request concurrency, and latency
during representative query and mutation workloads before selecting limits.

## Phase 5 validation outcome

Query-only runtime validation completed successfully on `bluefield3-101`:

- the native image `cloudstack-bluefield-integration:local` built and ran as
  `linux/arm64` with runtime UID/GID 10001;
- supplementary group 0 provided PoC access to the `root:root 0660` socket;
- the deployment token was absent from image inspection data and image history;
- the image healthcheck queried only `/health/ready`;
- mock and CLI containers became healthy, with successful liveness and
  readiness checks;
- missing authentication produced HTTP 401 with a Bearer challenge, while
  authenticated mock and CLI available-port queries succeeded;
- CLI mode accessed the read-only mounted executable and control-socket
  directory and parsed both uplink and VF representor formats;
- before-and-after eSwitch state, vSwitch output, and available-port output
  were unchanged;
- the standalone `eswitch-management` container remained running and healthy;
- `zona-01` connectivity used an encrypted SSH tunnel while the API remained
  bound to BlueField loopback, and the temporary copied client token was
  removed afterward;
- `docker compose down` removed only the Integration API container and network;
- no real create, delete, attach, or detach command was executed.

## Phase 6.4B mock-runtime outcome

On 2026-09-05, the Phase 6.4A allocation specification passed isolated native
ARM64 mock-runtime validation. Docker Buildx successfully built
`cloudstack-bluefield-integration:phase64b-mock`; the legacy builder was not
suitable because it did not support the Dockerfile's `COPY --chmod`. The
runtime used UID/GID 10001 and the expected entrypoint, and the image metadata
and history contained no API token.

The mock container was bound only to `127.0.0.1:18082` with bridge networking,
a read-only root filesystem, all capabilities dropped, no-new-privileges, and
the hardened 16 MiB `/tmp` tmpfs. It had no bind mounts and therefore no
access to the real executable, control socket, Docker socket, hugepages, host
devices, or eSwitch configuration. Health checks passed, unauthenticated API
access was rejected, and authenticated mock queries succeeded.

Because the mock adapter initially has no vSwitch, the test created mock
vSwitch 101 before allocating. The mock-only allocation selected representor
port 1 rather than uplink port 0, removed it from mock availability, replayed
idempotently, and rejected conflicting key reuse. These were in-memory mock
mutations only. Real vSwitch and available-port query results and their
SHA-256 checksums were unchanged, while the independent daemon remained
running and healthy with 12 ports, 4 assigned, 8 available, one vSwitch, and
4 FDB entries. No real eSwitch, VF, VM, sysfs, or CloudStack mutation occurred.

The isolated container and temporary validation artifacts were removed; the
mock image was retained. CLI allocation remains disabled. This validation
does not alter the production gates below or make process-local locking and
in-memory persistence suitable for production.

## Phase 6 prerequisites

Before real mutation validation or CloudStack integration, obtain explicit
approval and a change window, select isolated test identifiers and rollback
criteria with the eSwitch/DOCA owner, approve the permanent secure transport,
replace supplementary group 0 with a dedicated production socket group, and
define secret ownership and monitoring. Measure the Integration API under
representative workloads before choosing resource limits. Phase 5 completion
does not authorize mutation commands.
