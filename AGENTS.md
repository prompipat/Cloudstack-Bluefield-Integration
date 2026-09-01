# CloudStack–BlueField Integration API

## Objective

Build a Python FastAPI service that provides a REST interface between
Apache CloudStack and the NVIDIA BlueField eSwitch Management daemon.

## Architecture

- CloudStack calls this service through REST/JSON.
- The service communicates with `eswitch-management` through its Unix socket.
- Default socket: `/run/eswitch-management/control.sock`.
- Do not invoke `eswitchctl` with `subprocess` for normal operations.
- Use the text control protocol documented in `docs/CLI.md`.
- CloudStack is responsible for mapping a returned VF index to the host PCI
  address through sysfs.
- The API must not directly modify CloudStack production state.

## Initial REST scope

- `GET /health`
- `POST /api/v1/vswitches`
- `DELETE /api/v1/vswitches/{vswitch_id}`
- `GET /api/v1/ports/available`
- `POST /api/v1/vswitches/{vswitch_id}/ports`
- `DELETE /api/v1/vswitches/{vswitch_id}/ports/{port_id}`

## Port representation

Parse output such as:

`DPDK port 5 (host=1 pf=0 vf=4)`

into:

- `port_id`: 5
- `type`: representor
- `host`: 1
- `pf`: 0
- `vf_index`: 4

The uplink format must also be supported:

`DPDK port 0 (uplink/parent)`

## Engineering rules

- Run in mock mode during development on the CloudStack host.
- Access the real Unix socket only when deployed on BlueField.
- Validate vSwitch IDs as integers from 1 through 65535.
- Never construct shell commands from untrusted input.
- Never commit passwords, tokens, SSH keys, or `.env`.
- Separate API routes, business services, adapters, models, and configuration.
- Map daemon `ERR` responses to explicit API errors.
- Add unit tests for every response parser.
- Add integration tests using a fake Unix socket server.
- Preserve backward compatibility in the public REST API.
- Run formatting, linting, type checking, and tests before committing.

## Commands

Install:

`python -m pip install -e ".[dev]"`

Tests:

`pytest`

Lint:

`ruff check .`

Format:

`ruff format .`

Type check:

`mypy`
