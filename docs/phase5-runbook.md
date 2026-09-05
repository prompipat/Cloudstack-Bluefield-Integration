# Phase 5: BlueField Query-Only Validation Runbook

This runbook validates the Integration API against the active eSwitch runtime
without changing eSwitch state. The existing `eswitch-management` container is
independent and must not be managed by any command in this runbook.

## Safety boundary and known gate

Allowed eSwitch queries are `--help`, `status`, and
`list-port-available`. API checks are limited to `GET` requests. Do not run
any real `vs-create`, `vs-delete`, `vs-port-attach`, or
`vs-port-detach` command.

All `/api/v1/*` routes require
`Authorization: Bearer <INTEGRATION_API_TOKEN>`. Health routes remain public.
The token is a plaintext credential at the HTTP layer; Bearer authentication
does not encrypt the connection. Remote zona-01 testing remains blocked until
the team confirms a protected management network, TLS termination, or another
approved secure transport.

`X-Request-ID` is correlation metadata, not authentication.

Phase 6.4A adds an allocation endpoint for mock/fake testing only. In this
runbook's real CLI mode it returns HTTP 503 with `allocation_mock_only` before
any adapter invocation. Do not call it during query-only validation.

## Successful validation result

Phase 5 completed successfully on `bluefield3-101`. The native image
`cloudstack-bluefield-integration:local` ran as `linux/arm64` with UID/GID
10001; supplementary group 0 provided the PoC socket access. Image inspection
and history contained no deployment token, and the healthcheck called only
`/health/ready`.

Both mock and CLI containers became healthy. Public liveness and readiness
succeeded, missing authentication returned HTTP 401 with a Bearer challenge,
and authenticated available-port queries succeeded. CLI mode accessed the
read-only mounted executable and socket directory and correctly parsed both
uplink and VF representor results.

The eSwitch state and available-port output were unchanged before and after
validation. The standalone `eswitch-management` container remained running
and healthy. Connectivity from `zona-01` was validated through an encrypted
SSH tunnel while the API stayed bound to BlueField loopback; the temporary
client-side token copy was then removed. Rollback removed only the Integration
API container and network. No real create, delete, attach, or detach command
was run.

## 1. Pre-deployment checks

### On zona-01

Run from the repository checkout:

```bash
cd /home/prompipat/projects/cloudstack-bluefield-integration
git branch --show-current
git status --short
git log -1 --oneline
git diff --check
ruff format --check .
ruff check .
mypy
pytest
sh -n docker/entrypoint.sh docker/fake-eswitchctl
```

Expected branch: `feature/initial-intregration-api`. Record the commit ID that
will be transferred. Do not transfer an unreviewed dirty worktree.

Check that secrets and runtime files are not tracked:

```bash
git ls-files '.env' '*.key' '*.pem'
git check-ignore .env
```

The first command must produce no output. The second must identify `.env` as
ignored.

### On bluefield3-101

After explicit approval to access BlueField:

```bash
hostname
uname -m
docker version
docker compose version
stat -c '%U:%G %a %F %n' /usr/local/bin/eswitchctl
stat -c '%U:%G %a %F %n' /run/eswitch-management
stat -c '%U:%G %a %F %n' /run/eswitch-management/control.sock
```

Expected: host `bluefield3-101`, architecture `aarch64`, executable
`root:root 755`, directory `root:root 755`, and socket `root:root 660`.

Do not inspect the standalone daemon for secrets and do not change its
configuration, permissions, or lifecycle.

## 2. Source transfer or checkout

The configured repository remote is
`git@github.com:prompipat/Cloudstack-Bluefield-Integration.git`.

### On bluefield3-101

For a fresh checkout:

```bash
git clone git@github.com:prompipat/Cloudstack-Bluefield-Integration.git \
  cloudstack-bluefield-integration
cd cloudstack-bluefield-integration
git switch feature/initial-intregration-api
git status --short
git log -1 --oneline
```

For an existing checkout:

```bash
cd cloudstack-bluefield-integration
git fetch --all --prune
git switch feature/initial-intregration-api
git pull --ff-only
git status --short
git log -1 --oneline
```

The commit ID must match the reviewed zona-01 commit and status must be clean.

## 3. Local Compose environment

### On bluefield3-101

Generate a unique 256-bit token without printing it, then write it only to the
ignored deployment `.env`:

```bash
set +x
INTEGRATION_API_TOKEN="$(openssl rand -hex 32)"
test "${#INTEGRATION_API_TOKEN}" -ge 32
umask 077
{
  printf '%s\n' 'ESWITCH_ADAPTER_MODE=mock'
  printf '%s\n' 'INTEGRATION_API_BIND_ADDRESS=127.0.0.1'
  printf 'INTEGRATION_API_TOKEN=%s\n' "$INTEGRATION_API_TOKEN"
} > .env
chmod 0600 .env
unset INTEGRATION_API_TOKEN
git check-ignore .env
stat -c '%a %n' .env
```

Expected mode: `600`. Do not print, commit, or place the token in a shell
script. Compose injects it as an environment variable, never as an application
command-line argument.

Validate Compose without rendering the secret:

```bash
docker compose config --quiet
docker compose config --services
docker compose config --images
```

The service output must be only `integration-api`; the image must be
`cloudstack-bluefield-integration:local`. The configuration must not contain
`eswitch-management`, `depends_on`, host networking, privileged mode,
Docker socket, hugepages, device mounts, or `/var/lib/eswitch-management`.

## 4. Capture the pre-deployment query baseline

### On bluefield3-101

These host commands are query-only:

```bash
/usr/local/bin/eswitchctl status
/usr/local/bin/eswitchctl list-port-available
```

Expected status baseline at the prior observation was 12 ports, 4 assigned,
8 available, one vSwitch, and 4 FDB entries. Record only counts and command
success in deployment notes. Do not copy MAC addresses or full operational
logs into the repository.

If current counts differ, stop and have the eSwitch/DOCA owner confirm the
active topology before continuing.

## 5. Native ARM64 image build

### On bluefield3-101

```bash
docker compose build integration-api
docker image inspect cloudstack-bluefield-integration:local \
  --format 'image={{.RepoTags}} os={{.Os}} arch={{.Architecture}} user={{.Config.User}}'
```

Expected: `os=linux`, `arch=arm64`, and `user=10001:10001`.

Confirm the runtime architecture without starting the API:

```bash
docker run --rm --entrypoint uname \
  cloudstack-bluefield-integration:local -m
```

Expected: `aarch64`.

For a loader or GLIBC failure later, verify the mounted executable without
executing it:

```bash
file /usr/local/bin/eswitchctl
objdump -T /usr/local/bin/eswitchctl \
  | sed -n 's/.*GLIBC_\([0-9.]*\).*/GLIBC_\1/p' | sort -Vu | tail -1
docker run --rm --entrypoint /usr/bin/ldd \
  --volume /usr/local/bin/eswitchctl:/usr/local/bin/eswitchctl:ro \
  cloudstack-bluefield-integration:local /usr/local/bin/eswitchctl
```

Expected maximum requirement: `GLIBC_2.34`; Bookworm supplies GLIBC 2.36.

## 6. Mock-mode container smoke test

### On bluefield3-101

This intentionally mounts neither the real executable nor the real socket:

```bash
docker run --detach --name eswitch-api-mock-smoke \
  --publish 127.0.0.1:8081:8081 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m,mode=1777 \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --user 10001:10001 \
  --env-file .env \
  --env ESWITCH_ADAPTER_MODE=mock \
  cloudstack-bluefield-integration:local
curl --fail --show-error --include \
  -H 'X-Request-ID: phase5-mock-live' \
  http://127.0.0.1:8081/health/live
curl --fail --show-error --include \
  -H 'X-Request-ID: phase5-mock-ready' \
  http://127.0.0.1:8081/health/ready
docker inspect eswitch-api-mock-smoke --format '{{json .State.Health}}'
docker logs --tail 100 eswitch-api-mock-smoke
docker stop eswitch-api-mock-smoke
docker rm eswitch-api-mock-smoke
```

Expected bodies: `{"status":"live"}` and `{"status":"ready"}`.

## 7. Fake mutation-rejection test

### On bluefield3-101

This command invokes only the repository's fake executable. It must exit 1 and
must not access the real socket:

```bash
docker run --rm \
  --entrypoint /usr/local/bin/eswitchctl \
  --volume "$PWD/docker/fake-eswitchctl:/usr/local/bin/eswitchctl:ro" \
  cloudstack-bluefield-integration:local vs-create --id 1
test "$?" -eq 1
```

Because a nonzero Docker exit terminates an `&&` chain, run the two lines
interactively and verify the fake error says it rejects mutation commands.

## 8. Mounted query checks

### On bluefield3-101

Each command uses Compose service `integration-api` and only the two declared
read-only host mounts:

```bash
docker compose run --rm --no-deps \
  --entrypoint /usr/local/bin/eswitchctl \
  integration-api --help
docker compose run --rm --no-deps \
  --entrypoint /usr/local/bin/eswitchctl \
  integration-api status
docker compose run --rm --no-deps \
  --entrypoint /usr/local/bin/eswitchctl \
  integration-api list-port-available
```

Expected: help exits 0; status starts with `OK` and reports
`state=running`; available ports start with `OK` and use only documented
uplink or host/PF/VF formats.

## 9. Start the API in CLI mode on loopback

### On bluefield3-101

Preserve the token and switch the ignored deployment environment to CLI mode:

```bash
sed -i 's/^ESWITCH_ADAPTER_MODE=.*/ESWITCH_ADAPTER_MODE=cli/' .env
chmod 0600 .env
docker compose config --quiet
docker compose up --detach integration-api
docker compose ps integration-api
```

A missing, empty, or shorter-than-32-character token prevents CLI-mode startup.
Do not declare or manage `eswitch-management` in this Compose project.

## 10. Identity, mounts, and isolation

### On bluefield3-101

```bash
docker compose exec integration-api id
docker inspect "$(docker compose ps --quiet integration-api)" \
  --format 'privileged={{.HostConfig.Privileged}} readonly={{.HostConfig.ReadonlyRootfs}} network={{.HostConfig.NetworkMode}} capdrop={{json .HostConfig.CapDrop}} security={{json .HostConfig.SecurityOpt}} groups={{json .HostConfig.GroupAdd}} mounts={{json .Mounts}}'
docker compose exec integration-api /bin/sh -c \
  'test ! -w / && test -w /tmp && test ! -w /usr/local/bin/eswitchctl && test ! -w /run/eswitch-management'
```

Expected:

- UID/GID `10001/10001`, supplementary group `0`;
- `privileged=false` and `readonly=true`;
- project network, not `host`;
- capability drop includes `ALL`;
- no-new-privileges is present;
- only port 8081 is published;
- executable and socket directory mounts are read-only;
- only `/tmp` is the required writable tmpfs;
- no Docker socket, hugepages, devices, or eSwitch configuration mount.

## 11. API health and query validation

### On bluefield3-101

Health calls intentionally omit authentication:

```bash
curl --fail --show-error --include \
  -H 'X-Request-ID: phase5-live' \
  http://127.0.0.1:8081/health/live
curl --fail --show-error --include \
  -H 'X-Request-ID: phase5-ready' \
  http://127.0.0.1:8081/health/ready
```

Load the ignored deployment token without printing it. Pass the Authorization
header to curl through standard input rather than its command-line arguments:

```bash
set +x
INTEGRATION_API_TOKEN="$(
python3 - <<'PY'
from pathlib import Path

for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("INTEGRATION_API_TOKEN="):
        print(line.split("=", 1)[1])
        break
else:
    raise SystemExit("INTEGRATION_API_TOKEN not found")
PY
)"
export INTEGRATION_API_TOKEN
printf '%s\n' \
  'url = "http://127.0.0.1:8081/api/v1/ports/available"' \
  'header = "X-Request-ID: phase5-available"' \
  "header = \"Authorization: Bearer ${INTEGRATION_API_TOKEN}\"" \
  | curl --fail --show-error --include --config -
unset INTEGRATION_API_TOKEN
```

Expected: HTTP 200 for all three, request IDs echoed, readiness body
`{"status":"ready"}`, and a structured available-port JSON array. Missing,
wrong, malformed, empty, or non-Bearer credentials must return the same HTTP
401 response and `WWW-Authenticate: Bearer`.

CLI mode returns HTTP 404 for `/docs`, `/redoc`, and `/openapi.json`.

## 12. Logs and health

### On bluefield3-101

```bash
docker inspect "$(docker compose ps --quiet integration-api)" \
  --format '{{json .State.Health}}'
docker compose logs --no-color --tail 200 integration-api
```

Confirm request ID, operation, result, and duration fields are present. Do not
copy raw daemon logs or MAC addresses into the repository. `LOG_LEVEL` is
currently configuration-only and is not wired into Uvicorn logging.

Repeated FDB removal retry messages in the standalone daemon are for the
eSwitch/DOCA owner. Do not attempt to fix them from this API and do not inspect
or restart that container.

## 13. Compare the post-deployment baseline

### On bluefield3-101

```bash
/usr/local/bin/eswitchctl status
/usr/local/bin/eswitchctl list-port-available
```

Counts and available-port identities must match the pre-deployment query
baseline unless the eSwitch owner confirms an independent topology change.
The Integration API checks must not alter state.

## 14. zona-01 connectivity gate

Remote validation remains blocked until the team confirms a protected
management network, TLS termination, or another approved secure transport.
Bearer authentication alone does not make plaintext HTTP safe. After that
approval, replace `<BLUEFIELD_MANAGEMENT_IP>` with the approved address.

### On bluefield3-101

```bash
sed -i \
  's/^INTEGRATION_API_BIND_ADDRESS=.*/INTEGRATION_API_BIND_ADDRESS=<BLUEFIELD_MANAGEMENT_IP>/' \
  .env
chmod 0600 .env
docker compose config --quiet
docker compose up --detach --force-recreate integration-api
ss -ltnp | grep ':8081'
```

### On zona-01

Health remains unauthenticated:

```bash
curl --fail --show-error --include \
  -H 'X-Request-ID: zona01-live' \
  http://<BLUEFIELD_MANAGEMENT_IP>:8081/health/live
curl --fail --show-error --include \
  -H 'X-Request-ID: zona01-ready' \
  http://<BLUEFIELD_MANAGEMENT_IP>:8081/health/ready
```

For the query-only operational endpoint, read the deployment token through a
non-echoing prompt. Do not put it in shell history or a committed script:

```bash
set +x
read -r -s -p 'Integration API token: ' INTEGRATION_API_TOKEN
printf '\n'
printf '%s\n' \
  'url = "http://<BLUEFIELD_MANAGEMENT_IP>:8081/api/v1/ports/available"' \
  'header = "X-Request-ID: zona01-available"' \
  "header = \"Authorization: Bearer ${INTEGRATION_API_TOKEN}\"" \
  | curl --fail --show-error --include --config -
unset INTEGRATION_API_TOKEN
```

The address remains a deliberate placeholder. Execute these commands only
after the transport or protected-network gate is approved.

## 15. Failure diagnosis

### Architecture or loader

On bluefield3-101:

```bash
uname -m
docker image inspect cloudstack-bluefield-integration:local \
  --format '{{.Os}}/{{.Architecture}}'
file /usr/local/bin/eswitchctl
```

An `exec format error` indicates an architecture mismatch. A missing GLIBC
symbol indicates an incompatible runtime image or mounted binary.

### Executable, permission, or socket

On bluefield3-101:

```bash
stat -c '%U:%G %a %F %n' /usr/local/bin/eswitchctl
stat -c '%U:%G %a %F %n' /run/eswitch-management
stat -c '%U:%G %a %F %n' /run/eswitch-management/control.sock
docker compose exec integration-api id
docker compose exec integration-api ls -l /usr/local/bin/eswitchctl
docker compose exec integration-api ls -ld /run/eswitch-management
docker compose exec integration-api ls -l /run/eswitch-management/control.sock
```

Do not change permissions. The PoC depends on supplementary group 0;
production must use a dedicated socket group.

### Timeout, ERR, unsuccessful exit, or malformed output

On bluefield3-101:

```bash
docker compose exec integration-api /usr/local/bin/eswitchctl status
docker compose logs --no-color --tail 200 integration-api
curl --show-error --include http://127.0.0.1:8081/health/ready
```

Readiness should return HTTP 503 for dependency failures. Do not retry with a
mutation command.

### Authentication

On bluefield3-101, confirm that the configured value exists and meets the
length requirement without printing it:

```bash
docker compose exec integration-api /bin/sh -c \
  'test -n "$INTEGRATION_API_TOKEN" && test "${#INTEGRATION_API_TOKEN}" -ge 32'
curl --show-error --include \
  http://127.0.0.1:8081/api/v1/ports/available
```

The unauthenticated request must return HTTP 401 with the generic body
`{"detail":"Invalid or missing bearer token"}` and
`WWW-Authenticate: Bearer`. Wrong, malformed, empty, and unsupported-scheme
credentials return the same response. Authentication does not call
`eswitchctl`; a readiness failure is diagnosed separately.

### Network

After the approved transport or protected-network gate only:

```bash
ss -ltnp | grep ':8081'
```

On zona-01:

```bash
curl --connect-timeout 5 --show-error --include \
  http://<BLUEFIELD_MANAGEMENT_IP>:8081/health/live
```

Distinguish refused connections from timeouts and HTTP errors before changing
network policy.

## 16. Token rotation

### On bluefield3-101

Rotate only the Integration API credential. This recreates the API container
and neither restarts nor modifies `eswitch-management`:

```bash
set +x
INTEGRATION_API_TOKEN="$(
python3 - <<'PY'
from pathlib import Path

for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("INTEGRATION_API_TOKEN="):
        print(line.split("=", 1)[1])
        break
else:
    raise SystemExit("INTEGRATION_API_TOKEN not found")
PY
)"
export INTEGRATION_API_TOKEN
INTEGRATION_API_TOKEN="$(openssl rand -hex 32)"
test "${#INTEGRATION_API_TOKEN}" -ge 32
umask 077
{
  printf 'ESWITCH_ADAPTER_MODE=%s\n' "$ESWITCH_ADAPTER_MODE"
  printf 'INTEGRATION_API_BIND_ADDRESS=%s\n' "$INTEGRATION_API_BIND_ADDRESS"
  printf 'INTEGRATION_API_TOKEN=%s\n' "$INTEGRATION_API_TOKEN"
} > .env.new
chmod 0600 .env.new
mv .env.new .env
unset ESWITCH_ADAPTER_MODE INTEGRATION_API_BIND_ADDRESS INTEGRATION_API_TOKEN
docker compose config --quiet
docker compose up --detach --force-recreate integration-api
curl --fail --show-error http://127.0.0.1:8081/health/ready
```

Distribute the new token only through the site's approved secret channel.
The previous token becomes invalid when the replacement API container starts.

## 17. Clean shutdown and rollback

### On bluefield3-101

Stop and remove only the Integration API Compose project:

```bash
docker compose down
```

This is the exact rollback command. It must not stop, restart, recreate, or
otherwise affect the standalone `eswitch-management` container.

Verify the API listener is gone without inspecting or changing daemon state:

```bash
ss -ltnp | grep ':8081' || true
```

Retain build logs and summarized query results outside the repository
according to the site's operational log policy.

## Phase 6 prerequisites

Phase 5 success does not authorize mutation testing. Before Phase 6:

- obtain explicit mutation approval and schedule an operational change window;
- coordinate isolated test vSwitch IDs and ports, rollback steps, and success
  criteria with the eSwitch/DOCA owner because the target carries traffic;
- approve the permanent zona-01 transport or protected-network policy;
- replace the PoC supplementary group 0 with a dedicated production socket
  group;
- approve token custody, distribution, and rotation procedures;
- define monitoring and measure representative CPU, RSS, latency, concurrency,
  and failure behavior before choosing container resource limits;
- keep the Integration API independent from the daemon container and preserve
  the prohibition on Docker socket, privileged mode, host networking,
  hugepages, devices, and daemon configuration mounts.
