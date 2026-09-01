# CloudStack–BlueField Integration API

REST integration service between Apache CloudStack and NVIDIA BlueField
eSwitch Management.

## Current status

Phase 2 is complete. The repository contains:

- strict application and adapter configuration;
- validated request and response models;
- parsers for the documented `OK`, `ERR`, status, mutation, representor-port,
  and uplink-port responses;
- an allowlisted adapter protocol;
- a deterministic, stateful mock adapter;
- unit tests for configuration, models, parsers, and mock behavior.

The production CLI adapter, FastAPI routes, and container runtime are not yet
implemented. No automated test contacts the production daemon.

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

## Validation

```bash
ruff format --check .
ruff check .
mypy
pytest
```

The Phase 2 baseline is 57 passing unit tests with Ruff and strict mypy also
passing.

## Next phase

Phase 3 begins only after explicit approval. Implement the CLI adapter with
mocked subprocess tests first, then add the FastAPI application, required
health and v1 routes, sanitized exception mapping, request IDs, operation
logging, and API tests. Do not contact the real BlueField or execute mutation
commands against it.

To resume development:

```bash
cd /home/prompipat/projects/cloudstack-bluefield-integration
source .venv/bin/activate
git status --short
ruff check .
mypy
pytest
```
