# CloudStack–BlueField Integration API

## Objective

Build a Python FastAPI service that provides a REST interface between
Apache CloudStack and the NVIDIA BlueField eSwitch Management daemon.

## System responsibilities

### Apache CloudStack

- Calls the Integration API through REST/JSON.
- Requests creation and deletion of virtual switches.
- Requests allocation, attachment, and detachment of DPDK ports.
- Receives `host`, `pf`, and `vf_index` from the Integration API.
- Maps `vf_index` to the host PCI address through Linux sysfs.
- Uses the resolved PCI address for VM PCI passthrough.

Example host-side mapping:

`readlink -f /sys/bus/pci/devices/0000:84:00.0/virtfn4`

### Integration API

- Runs as a Python container on NVIDIA BlueField.
- Exposes a controlled REST API to Apache CloudStack.
- Invokes the mounted `/usr/local/bin/eswitchctl` executable.
- Parses the `eswitchctl` response into structured JSON.
- Does not allow callers to execute arbitrary commands.
- Does not perform host-side VF-index-to-PCI mapping.

### BlueField host

- Runs the existing `eswitch-management` daemon.
- Provides `/usr/local/bin/eswitchctl`.
- Provides `/run/eswitch-management/control.sock`.
- Does not run another DOCA, DPDK, or eswitch-management process inside
  the Integration API container.

## Deployment architecture

The runtime flow is:

Apache CloudStack
→ REST API
→ FastAPI container on BlueField
→ mounted `/usr/local/bin/eswitchctl`
→ mounted `/run/eswitch-management/control.sock`
→ host `eswitch-management` daemon
→ DOCA/DPDK hardware pipeline

The API container must mount:

- `/usr/local/bin/eswitchctl` as read-only.
- `/run/eswitch-management` as a read-only directory.

Mount the socket directory rather than only the socket file so a socket
recreated after an eswitch-management restart remains visible.

The existing daemon runs independently as the standalone Docker container
`eswitch-management`; it is not a systemd service and must not be added to or
managed by the Integration API Compose project. The Integration API must never
start, stop, restart, recreate, or modify that container, inspect secrets from
it, or mount the Docker socket. It also must not use privileged mode, host
networking, hugepages, DOCA device mounts, or `/var/lib/eswitch-management`.

The target environment carries active traffic. Phase 5 runtime validation is
strictly query-only: `--help`, `status`, `list-port-available`, liveness, and
readiness. Do not commit raw MAC addresses or full operational logs. Observed
FDB removal retries belong to the daemon/DOCA owner and are outside this API's
responsibility.

## BlueField runtime information

- Operating system: Ubuntu 24.04.4 LTS
- Architecture: ARM64/aarch64
- Host glibc: 2.39
- eswitchctl architecture: ARM aarch64
- Maximum required eswitchctl GLIBC symbol: GLIBC_2.34
- eswitchctl dependencies: libc and the aarch64 dynamic loader
- eswitchctl path: `/usr/local/bin/eswitchctl`
- eswitchctl ownership and mode: `root:root 0755`
- Control socket: `/run/eswitch-management/control.sock`
- Control socket ownership and mode: `root:root 0660`

Use a Linux ARM64 container image with GLIBC 2.34 or newer.
`python:3.12-slim-bookworm` is compatible because Debian Bookworm provides
GLIBC 2.36.

## Initial REST scope

- `GET /health/live`
- `GET /health/ready`
- `POST /api/v1/vswitches`
- `DELETE /api/v1/vswitches/{vswitch_id}`
- `GET /api/v1/ports/available`
- `POST /api/v1/vswitches/{vswitch_id}/ports`
- `DELETE /api/v1/vswitches/{vswitch_id}/ports/{port_id}`

Future atomic allocation endpoint:

- `POST /api/v1/vswitches/{vswitch_id}/ports/allocate`

The atomic allocation endpoint should select and attach an available port
inside one protected operation to prevent concurrent requests from selecting
the same port.

## Canonical eswitchctl commands

Create a virtual switch:

`eswitchctl vs-create --id <id>`

Delete a virtual switch:

`eswitchctl vs-delete --id <id>`

List available ports:

`eswitchctl list-port-available`

Attach a port:

`eswitchctl vs-port-attach --id <id> --port <port-id>`

Detach a port:

`eswitchctl vs-port-detach --id <id> --port <port-id>`

Daemon readiness:

`eswitchctl status`

Use the complete command contract in `docs/CLI.md`.

## Command adapter requirements

- Invoke `/usr/local/bin/eswitchctl` using Python subprocess APIs.
- Always pass executable and arguments as a list.
- Never use `shell=True`.
- Never construct commands through string interpolation for shell execution.
- Use a configurable executable path.
- Use a configurable timeout.
- Capture stdout, stderr, and process exit code.
- Treat exit code 0 and an `OK` response as success.
- Treat exit code 1 or an `ERR` response as a daemon error.
- Handle timeout, executable-not-found, permission, and OS errors.
- Do not expose a generic command execution endpoint.
- Use an allowlisted mapping between API operations and CLI commands.
- Validate all numeric arguments before invoking the executable.
- Preserve the daemon error code and message in internal error objects.
- Do not expose unnecessary internal information to untrusted API clients.

## Validation

- vSwitch ID must be an integer from 1 through 65535.
- Port ID must be an integer from 0 through 65535.
- Reject booleans as integer values.
- Reject extra JSON fields where appropriate.
- Do not accept an executable path or command name from an API request.

## Port response parsing

Parse:

`DPDK port 5 (host=1 pf=0 vf=4)`

into:

- `port_id`: 5
- `type`: `representor`
- `host`: 1
- `pf`: 0
- `vf_index`: 4

Parse:

`DPDK port 0 (uplink/parent)`

into:

- `port_id`: 0
- `type`: `uplink`
- `host`: null
- `pf`: null
- `vf_index`: null

Malformed output must produce an explicit adapter or parsing error.

## Adapter modes

Support two adapter modes:

- `mock`: used for development and automated tests on zona-01.
- `cli`: used inside the container running on BlueField.

Do not require BlueField hardware or eswitchctl for unit tests.

## Container requirements

- Target platform: `linux/arm64`.
- Recommended base image: `python:3.12-slim-bookworm`.
- Run as non-root UID/GID 10001.
- For the PoC, add supplementary group 0 to access the root:root 0660 socket.
- Do not run in privileged mode.
- Drop all Linux capabilities.
- Set `no-new-privileges`.
- Use a read-only root filesystem.
- Provide a small writable `/tmp` using tmpfs.
- Do not copy or install eswitchctl into the image.
- Do not include credentials in the image.
- Expose API port 8081.
- Add a container healthcheck.

Production should replace supplementary group 0 with a dedicated socket group
such as `eswitch-api`.

## Health endpoints

### `GET /health/live`

- Confirms the FastAPI process is running.
- Must not call eswitchctl.
- Returns HTTP 200 while the API process is healthy.

### `GET /health/ready`

- In mock mode, checks mock-adapter readiness.
- In CLI mode, executes `/usr/local/bin/eswitchctl status`.
- Returns HTTP 200 only when eswitchctl can contact eswitch-management.
- Returns HTTP 503 when the executable, socket, or daemon is unavailable.

## Testing requirements

- Unit-test all response parsers.
- Mock subprocess execution in unit tests.
- Test exit code 0 with an OK response.
- Test exit code 1 with an ERR response.
- Test command timeout.
- Test executable-not-found.
- Test permission errors.
- Test malformed output.
- Test available representor ports.
- Test the uplink/parent format.
- Test API input validation.
- Add a container smoke test using a fake eswitchctl executable.
- Do not invoke the production daemon in normal automated tests.

## Security requirements

- Never commit API tokens, passwords, SSH keys, or `.env`.
- Do not expose the API directly to an untrusted network.
- Bind or firewall the API to the CloudStack management network.
- Do not use privileged containers.
- Do not mount the Docker socket.
- Do not mount the complete host filesystem.
- Mount only the required binary and runtime socket directory.
- Log operation type, request ID, result, and duration.
- Do not log API secrets.

## API authentication

All routes under `/api/v1` require exactly
`Authorization: Bearer <token>`. The configured token comes only from
`INTEGRATION_API_TOKEN`, must contain at least 32 characters, and is handled
as a secret. Missing, invalid, malformed, or unsupported credentials return the
same HTTP 401 response with `WWW-Authenticate: Bearer`. Scheme matching is
case-insensitive; token matching is case-sensitive and constant-time.

Health routes remain unauthenticated. Mock mode may start without a token for
health-only development, but its operational routes return HTTP 401 until a
valid token is configured. CLI mode must fail startup if the token is missing,
empty, or too short. Swagger UI, ReDoc, and OpenAPI JSON are available in mock
mode and disabled in CLI mode.

Bearer authentication does not encrypt HTTP traffic. Remote access is
prohibited until a protected management network, TLS termination, or another
approved secure transport is confirmed. Never log the Authorization header or
place the token in URLs, request bodies, command-line arguments, committed
files, or container images.

## Host-side VF-to-PCI resolver boundary

The reference module `host_tools.vf_pci_resolver` runs on a selected KVM
Compute Host, never inside the BlueField Integration API container. It reads a
site-specific `/etc/cloudstack/bluefield-pf-map.toml` and host-local sysfs to
resolve `(host, pf, vf_index)` to a PCI BDF. It must remain read-only and must
not infer reservation, availability, or attachment safety. The committed
example mapping is generic and is not production configuration.

The module, mapping examples, fake sysfs tests, and host-side documentation
must remain outside the API wheel and container. Future CloudStack integration
belongs in the KVM Agent and requires an atomic allocation/reservation design;
this repository must not modify CloudStack during the reference-tool phase.

## Development commands

Install:

`python -m pip install -e ".[dev]"`

Run tests:

`pytest`

Lint:

`ruff check .`

Format:

`ruff format .`

Type check:

`mypy`
