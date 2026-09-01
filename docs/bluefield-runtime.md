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
