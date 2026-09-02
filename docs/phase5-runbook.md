# Phase 5: BlueField Query-Only Validation Runbook

This runbook validates the Integration API against the active eSwitch runtime
without changing eSwitch state. The existing `eswitch-management` container is
independent and must not be managed by any command in this runbook.

## Safety boundary and known gate

Allowed eSwitch queries are `--help`, `status`, and
`list-port-available`. API checks are limited to `GET` requests. Do not run
any real `vs-create`, `vs-delete`, `vs-port-attach`, or
`vs-port-detach` command.

Authentication is not implemented in the current repository. The former
`API_TOKEN` placeholder has been removed from `.env.example`, and there is no
authentication header to supply.
Therefore:

- loopback validation on `bluefield3-101` may proceed after approval;
- an authenticated available-port query cannot yet be performed;
- publishing port 8081 or querying it from `zona-01` is a hard stop until an
  authentication contract is approved and implemented.

`X-Request-ID` is correlation metadata, not authentication.

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

Create a local, ignored `.env` containing only non-secret Compose values:

```bash
umask 077
printf '%s\n' \
  'ESWITCH_ADAPTER_MODE=mock' \
  'INTEGRATION_API_BIND_ADDRESS=127.0.0.1' > .env
git check-ignore .env
```

Do not add `API_TOKEN`: the application does not implement authentication.
Do not commit `.env`.

Render and inspect configuration before starting anything:

```bash
docker compose config
```

The rendered project must contain only service `integration-api`. It must
not contain `eswitch-management`, `depends_on`, host networking, privileged
mode, Docker socket, hugepages, device mounts, or
`/var/lib/eswitch-management`.

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

Update the ignored local environment:

```bash
umask 077
printf '%s\n' \
  'ESWITCH_ADAPTER_MODE=cli' \
  'INTEGRATION_API_BIND_ADDRESS=127.0.0.1' > .env
docker compose config
docker compose up --detach integration-api
docker compose ps integration-api
```

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

```bash
curl --fail --show-error --include \
  -H 'X-Request-ID: phase5-live' \
  http://127.0.0.1:8081/health/live
curl --fail --show-error --include \
  -H 'X-Request-ID: phase5-ready' \
  http://127.0.0.1:8081/health/ready
curl --fail --show-error --include \
  -H 'X-Request-ID: phase5-available' \
  http://127.0.0.1:8081/api/v1/ports/available
```

Expected: HTTP 200 for all three, request IDs echoed, readiness body
`{"status":"ready"}`, and a structured available-port JSON array.

The available-port request above is unauthenticated because that is the exact
current API behavior. It is suitable only for loopback validation.

### Authentication hard stop

The required authenticated available-port query cannot be written or executed
against the current repository: no authentication scheme or header exists.
Do not invent an `Authorization`, `X-API-Key`, or other header. Approve and
implement authentication in a separate API change before continuing to remote
connectivity.

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

Remote validation is blocked until authentication is implemented and the
BlueField firewall permits TCP 8081 only from zona-01.

After those separate prerequisites are completed, replace
`<BLUEFIELD_MANAGEMENT_IP>` with the approved address.

### On bluefield3-101

```bash
umask 077
printf '%s\n' \
  'ESWITCH_ADAPTER_MODE=cli' \
  'INTEGRATION_API_BIND_ADDRESS=<BLUEFIELD_MANAGEMENT_IP>' > .env
docker compose up --detach --force-recreate integration-api
ss -ltnp | grep ':8081'
```

### On zona-01

The authentication header cannot be specified until its contract exists.
Once implemented, use the exact documented header with these endpoints:

```bash
curl --fail --show-error --include \
  <AUTHENTICATION_HEADER> \
  -H 'X-Request-ID: zona01-live' \
  http://<BLUEFIELD_MANAGEMENT_IP>:8081/health/live
curl --fail --show-error --include \
  <AUTHENTICATION_HEADER> \
  -H 'X-Request-ID: zona01-ready' \
  http://<BLUEFIELD_MANAGEMENT_IP>:8081/health/ready
curl --fail --show-error --include \
  <AUTHENTICATION_HEADER> \
  -H 'X-Request-ID: zona01-available' \
  http://<BLUEFIELD_MANAGEMENT_IP>:8081/api/v1/ports/available
```

These are deliberately placeholders and must not be executed as written.
Replace them only after the repository defines the authentication header.

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

HTTP 200 without credentials reflects the known missing-authentication gate,
not successful authentication. HTTP 401/403 behavior does not exist yet.

### Network

After authentication and firewall approval only:

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

## 16. Clean shutdown and rollback

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
