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
