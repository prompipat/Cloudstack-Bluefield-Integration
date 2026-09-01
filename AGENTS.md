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
