# CloudStack–BlueField Integration API

REST integration service between Apache CloudStack and NVIDIA BlueField
eSwitch Management.

## Current status

Phase 4 is complete. The repository contains:

- strict application and adapter configuration;
- validated request and response models;
- parsers for the documented `OK`, `ERR`, status, mutation, representor-port,
  and uplink-port responses;
- deterministic mock and allowlisted CLI adapters;
- a FastAPI application with separate liveness and readiness endpoints;
- all initial vSwitch and port REST endpoints;
- router-level static Bearer authentication for all operational API routes;
- sanitized adapter error responses, request IDs, and operation logging;
- production-disabled interactive API documentation;
- a hardened ARM64 container definition and BlueField compose configuration;
- unit, API, container-contract, and fake-CLI smoke tests.

Docker is unavailable in the current development environment, so the ARM64
image has not been built or started here. Automated tests never invoke
the production executable or contact the BlueField daemon.

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

The current baseline is 135 passing tests with Ruff and strict mypy also
passing.

## Next phase

Follow the query-only [Phase 5 runbook](docs/phase5-runbook.md). Set a unique
`INTEGRATION_API_TOKEN` of at least 32 characters and send it only as
`Authorization: Bearer <token>` to `/api/v1/*`. Health endpoints remain
public. Bearer authentication does not encrypt HTTP: remote zona-01 access
remains blocked until a protected management network, TLS termination, or
another approved secure transport is confirmed.

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
